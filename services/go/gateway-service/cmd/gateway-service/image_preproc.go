package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"log"
	"math"
	"net/http"
	"strings"
)

// ── Image Preprocessing Handler ───────────────────────────────────────

// imagePreprocRequest is the JSON body for POST /platform/utils/image-preprocess.
type imagePreprocRequest struct {
	Image     string `json:"image"`      // base64-encoded image (may include data URI prefix)
	MaxWidth  int    `json:"max_width"`  // max width before resize (default 1024)
	MaxHeight int    `json:"max_height"` // max height before resize (default 1024)
	Quality   int    `json:"quality"`    // JPEG/WebP quality 1-100 (default 85)
	StripEXIF bool   `json:"strip_exif"` // strip EXIF by re-encoding
	Format    string `json:"format"`     // target format: "webp", "jpeg", "png" (default "webp")
}

// imagePreprocResponse is the JSON response from the image preprocessor.
type imagePreprocResponse struct {
	Data           string `json:"data"`            // base64-encoded processed image
	Format         string `json:"format"`          // output format
	Width          int    `json:"width"`           // output image width
	Height         int    `json:"height"`          // output image height
	OriginalSize   int64  `json:"original_size"`   // original base64 decoded size in bytes
	CompressedSize int64  `json:"compressed_size"` // compressed size in bytes
	HasText        bool   `json:"has_text"`        // heuristic text detection
	Warning        string `json:"warning,omitempty"`
}

// newImagePreprocHandler returns an http.Handler for image preprocessing.
func newImagePreprocHandler() http.Handler {
	return http.HandlerFunc(handleImagePreprocess)
}

func handleImagePreprocess(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusMethodNotAllowed)
		json.NewEncoder(w).Encode(map[string]string{"error": "method not allowed"})
		return
	}

	w.Header().Set("Content-Type", "application/json")

	var req imagePreprocRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"})
		return
	}

	if req.Image == "" {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "image field is required"})
		return
	}

	// Set defaults
	if req.MaxWidth <= 0 {
		req.MaxWidth = 1024
	}
	if req.MaxHeight <= 0 {
		req.MaxHeight = 1024
	}
	if req.Quality <= 0 || req.Quality > 100 {
		req.Quality = 85
	}
	if req.Format == "" {
		req.Format = "webp"
	}

	// Strip data URI prefix if present
	imageB64 := req.Image
	if idx := strings.Index(imageB64, "base64,"); idx != -1 {
		imageB64 = imageB64[idx+7:]
	}

	rawBytes, err := base64.StdEncoding.DecodeString(imageB64)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid base64 image data"})
		return
	}
	originalSize := int64(len(rawBytes))

	// Decode the image
	img, format, err := image.Decode(bytes.NewReader(rawBytes))
	if err != nil {
		// Cannot decode natively — pass through with warning
		log.Printf("image-preproc: cannot decode image (%v), passing through", err)
		resp := imagePreprocResponse{
			Data:           req.Image,
			Format:         "original",
			Width:          0,
			Height:         0,
			OriginalSize:   originalSize,
			CompressedSize: originalSize,
			HasText:        false,
			Warning:        "unsupported_format_passthrough: " + err.Error(),
		}
		json.NewEncoder(w).Encode(resp)
		return
	}
	_ = format

	bounds := img.Bounds()
	srcW := bounds.Dx()
	srcH := bounds.Dy()

	// Strip EXIF and resize
	var processed image.Image
	if req.StripEXIF || srcW > req.MaxWidth || srcH > req.MaxHeight {
		// Re-encode to strip EXIF
		processed = img

		// Resize if exceeds max dimensions (maintain aspect ratio)
		newW, newH := srcW, srcH
		if srcW > req.MaxWidth || srcH > req.MaxHeight {
			ratio := math.Min(float64(req.MaxWidth)/float64(srcW), float64(req.MaxHeight)/float64(srcH))
			newW = int(float64(srcW) * ratio)
			newH = int(float64(srcH) * ratio)
			if newW < 1 {
				newW = 1
			}
			if newH < 1 {
				newH = 1
			}
		}

		if newW != srcW || newH != srcH {
			// Use nearest-neighbor scale via manual resampling (standard library compatible)
			processed = resizeNearest(processed, newW, newH)
		}
	} else {
		processed = img
	}

	// Encode to target format
	outW := processed.Bounds().Dx()
	outH := processed.Bounds().Dy()

	var outBuf bytes.Buffer
	outFormat := req.Format

	switch strings.ToLower(req.Format) {
	case "webp":
		// Go standard library does not include webp encoder.
		// Fall back to JPEG with a note.
		err = jpeg.Encode(&outBuf, processed, &jpeg.Options{Quality: req.Quality})
		outFormat = "jpeg"
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "encode failed: " + err.Error()})
			return
		}
	case "png":
		err = png.Encode(&outBuf, processed)
		outFormat = "png"
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "encode failed: " + err.Error()})
			return
		}
	default:
		// Default to JPEG
		err = jpeg.Encode(&outBuf, processed, &jpeg.Options{Quality: req.Quality})
		outFormat = "jpeg"
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "encode failed: " + err.Error()})
			return
		}
	}

	compressedBytes := outBuf.Bytes()
	compressedSize := int64(len(compressedBytes))

	// Heuristic text detection: check for high-contrast edges
	hasText := detectTextHeuristic(processed)

	resp := imagePreprocResponse{
		Data:           "data:image/" + outFormat + ";base64," + base64.StdEncoding.EncodeToString(compressedBytes),
		Format:         outFormat,
		Width:          outW,
		Height:         outH,
		OriginalSize:   originalSize,
		CompressedSize: compressedSize,
		HasText:        hasText,
	}
	if outFormat != req.Format && req.Format != "" {
		resp.Warning = "webp_encoder_not_available_fallback_to_" + outFormat
	}

	json.NewEncoder(w).Encode(resp)
}

// resizeNearest performs a simple nearest-neighbor resize.
// For production use, a bilinear/bicubic resampler is preferred,
// but this avoids external dependencies in the Go gateway.
func resizeNearest(src image.Image, newW, newH int) image.Image {
	bounds := src.Bounds()
	srcW, srcH := bounds.Dx(), bounds.Dy()

	dst := image.NewRGBA(image.Rect(0, 0, newW, newH))

	for y := 0; y < newH; y++ {
		for x := 0; x < newW; x++ {
			srcX := x * srcW / newW
			srcY := y * srcH / newH
			dst.Set(x, y, src.At(srcX+bounds.Min.X, srcY+bounds.Min.Y))
		}
	}
	return dst
}

// detectTextHeuristic checks for text-like patterns by looking for
// high-contrast edges in a downsampled version of the image.
// This is a simple heuristic, not a proper OCR engine.
func detectTextHeuristic(img image.Image) bool {
	bounds := img.Bounds()
	w, h := bounds.Dx(), bounds.Dy()

	// Downsample to a maximum of 128x128 for fast processing
	stepX := intMax(1, w/128)
	stepY := intMax(1, h/128)

	var edgeCount int
	var totalSamples int

	for y := bounds.Min.Y + stepY; y < bounds.Max.Y-stepY; y += stepY {
		for x := bounds.Min.X + stepX; x < bounds.Max.X-stepX; x += stepX {
			// Extract luminance for current and neighboring pixels
			cLum := luminance(img.At(x, y))
			rLum := luminance(img.At(x+stepX, y))
			dLum := luminance(img.At(x, y+stepY))

			// High contrast edge detection
			if absDiff(cLum, rLum) > 60 || absDiff(cLum, dLum) > 60 {
				edgeCount++
			}
			totalSamples++
		}
	}

	if totalSamples == 0 {
		return false
	}

	// If more than 8% of sampled pixels are on high-contrast edges,
	// the image likely contains text
	return float64(edgeCount)/float64(totalSamples) > 0.08
}

// luminance extracts a rough luminance value (0-255) from a color.
func luminance(c color.Color) int {
	r, g, b, _ := c.RGBA()
	// ITU BT.601 luma coefficients, scaled from 0-65535 to 0-255
	return int(0.299*float64(r>>8) + 0.587*float64(g>>8) + 0.114*float64(b>>8))
}

func absDiff(a, b int) int {
	if a > b {
		return a - b
	}
	return b - a
}

func intMax(a, b int) int {
	if a > b {
		return a
	}
	return b
}
