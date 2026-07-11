'use client';

import { useState, useRef, useEffect, type JSX, type ImgHTMLAttributes } from 'react';

// ── LazyImage — Intersection Observer-based lazy loading ─────────────────
//
// Delays image loading until the element is near the viewport. Falls back
// to a lightweight placeholder (low-res blur or solid bg) during loading.
// All images get loading="lazy" + decoding="async" for additional browser-
// level optimization.

interface LazyImageProps extends ImgHTMLAttributes<HTMLImageElement> {
  /** Aspect ratio for placeholder sizing (width/height). Default 16/9. */
  aspectRatio?: number;
  /** Low-quality placeholder source (tiny base64) for blur-up effect. */
  placeholderSrc?: string;
  /** Show a skeleton shimmer while loading. */
  shimmer?: boolean;
}

export default function LazyImage({
  src,
  alt = '',
  aspectRatio = 16 / 9,
  placeholderSrc,
  shimmer = true,
  className = '',
  style,
  ...imgProps
}: LazyImageProps): JSX.Element {
  const [isLoaded, setIsLoaded] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  const [hasError, setHasError] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    if (!imgRef.current) return;

    // Use native lazy loading as primary strategy, IntersectionObserver as fallback
    if ('loading' in HTMLImageElement.prototype) {
      setIsVisible(true);
      return;
    }

    observerRef.current = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observerRef.current?.disconnect();
        }
      },
      { rootMargin: '200px', threshold: 0.01 }
    );

    observerRef.current.observe(imgRef.current);

    return () => observerRef.current?.disconnect();
  }, []);

  const paddingBottom = `${(1 / aspectRatio) * 100}%`;
  const combinedStyle: React.CSSProperties = {
    position: 'relative',
    overflow: 'hidden',
    background: '#f0f0f0',
    ...style,
  };

  const handleLoad = () => setIsLoaded(true);
  const handleError = () => setHasError(true);

  return (
    <div className={`lazy-image-wrapper ${className}`} style={combinedStyle}>
      {/* Aspect ratio box */}
      <div style={{ width: '100%', paddingBottom }} />

      {/* Skeleton shimmer placeholder */}
      {shimmer && !isLoaded && !hasError && (
        <div
          className="lazy-image-shimmer"
          style={{
            position: 'absolute',
            inset: 0,
            background: 'linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.5s ease-in-out infinite',
          }}
        />
      )}

      {/* Low-quality placeholder */}
      {placeholderSrc && !isLoaded && (
        <img
          src={placeholderSrc}
          alt=""
          aria-hidden
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            filter: 'blur(20px)',
            transform: 'scale(1.1)',
            opacity: isLoaded ? 0 : 1,
            transition: 'opacity 0.3s ease',
          }}
        />
      )}

      {/* Error state */}
      {hasError && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: '#fafafa',
            color: '#999',
            fontSize: '14px',
          }}
        >
          <span>[img] 图片加载失败</span>
        </div>
      )}

      {/* Actual image */}
      {isVisible && !hasError && (
        <img
          ref={imgRef}
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onLoad={handleLoad}
          onError={handleError}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            opacity: isLoaded ? 1 : 0,
            transition: 'opacity 0.4s ease',
          }}
          {...imgProps}
        />
      )}

      {/* Shimmer keyframes injected once per document */}
      <style jsx>{`
        @keyframes shimmer {
          0% { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
      `}</style>
    </div>
  );
}
