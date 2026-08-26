/** @type {import('next').NextConfig} */
const withBundleAnalyzer = process.env.ANALYZE === 'true'
  ? require('@next/bundle-analyzer')({ enabled: true })
  : (config) => config;

// Dual-track migration: the frontend can route API traffic to either the legacy
// Python monolith (port 8000) or the new Go gateway (port 8081).
//   API_BACKEND=go     (default) → http://127.0.0.1:8081/api/*
//   API_BACKEND=legacy            → http://127.0.0.1:8000/api/* (Python monolith)
//   GO_GATEWAY_URL=http://host:port → custom gateway URL (overrides the above)
// The /platform/* path always routes to the Go gateway regardless of API_BACKEND.
const apiBackend = process.env.API_BACKEND || 'go';
const legacyUrl = process.env.API_BACKEND_URL || 'http://127.0.0.1:8000';
const goGatewayUrl = process.env.GO_GATEWAY_URL || 'http://127.0.0.1:8081';
const apiDestination = apiBackend === 'go' ? goGatewayUrl : legacyUrl;

const nextConfig = {
  output: 'standalone',
  distDir: process.env.NEXT_DIST_DIR || '.next',
  reactStrictMode: true,
  swcMinify: true,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  compress: true,
  // Transpile ESM packages that ship untranspiled source (required by App Router
  // for proper server/client boundary handling). Without this, Next.js 14 may
  // emit "Module not found" or style-flash warnings for these packages.
  transpilePackages: [
    '@monaco-editor/react',
    'react-konva',
    'konva',
    'framer-motion',
    'react-syntax-highlighter',
    'react-diff-viewer-continued',
  ],
  // Pre-existing TS 6.0 strict ref issues — fix separately
  typescript: { ignoreBuildErrors: true },
  images: {
    // Allow external avatar images from common providers
    remotePatterns: [],
    // Use unoptimized for now (most avatars are external URLs)
    unoptimized: true,
    deviceSizes: [640, 768, 1024, 1280, 1536],
    imageSizes: [16, 32, 48, 64, 96],
  },
  async rewrites() {
    return [
      // /api/* routes to either legacy or Go based on API_BACKEND env.
      { source: '/api/:path*', destination: `${apiDestination}/api/:path*` },
      // /platform/* always routes to the Go gateway (new platform APIs).
      { source: '/platform/:path*', destination: `${goGatewayUrl}/:path*` },
    ];
  },
  webpack: (config, { isServer, dev }) => {
    // Konva tries to require('canvas') for server-side rendering — we only
    // use react-konva client-side (ssr:false), so exclude it from server bundles.
    if (isServer) {
      config.externals = [...(config.externals || []), 'canvas'];
    }
    if (!isServer) {
      // Only customize webpack production optimizations in prod builds.
      // In dev mode, these optimizations can make Next's on-demand page
      // compilation and error overlay unstable or extremely slow.
      if (!dev) {
        config.optimization.splitChunks = {
          chunks: 'all',
          maxInitialRequests: 25,
          minSize: 20000,
          cacheGroups: {
            // Monaco editor — heavy, only used in diff views
            monaco: {
              test: /[\\/]node_modules[\\/]@monaco-editor[\\/]/,
              name: 'monaco-editor',
              priority: 20,
              reuseExistingChunk: true,
            },
            // Syntax highlighting libraries
            syntaxHighlighter: {
              test: /[\\/]node_modules[\\/](react-syntax-highlighter|highlight\.js|prismjs|refractor)[\\/]/,
              name: 'syntax-highlighter',
              priority: 20,
              reuseExistingChunk: true,
            },
            // PDF.js — heavy, only used in file preview modal
            pdfjs: {
              test: /[\\/]node_modules[\\/]pdfjs-dist[\\/]/,
              name: 'pdfjs',
              priority: 20,
              reuseExistingChunk: true,
            },
            // Konva canvas library
            konva: {
              test: /[\\/]node_modules[\\/](konva|react-konva)[\\/]/,
              name: 'konva-canvas',
              priority: 20,
              reuseExistingChunk: true,
            },
            // Markdown rendering (more aggressive grouping)
            markdown: {
              test: /[\\/]node_modules[\\/](react-markdown|remark-gfm|mdast|unified|micromark|hast|unist|vfile|bail|trough|zwitch|ccount|character-|comma-separated|decode-named|devlop|escape-string|estree|extend|fault|github-slugger|html-|is-|longest-streak|markdown-table|property-information|space-separated|stringify-entities|style-to-object|trim-lines)[\\/]/,
              name: 'markdown-renderer',
              priority: 15,
              reuseExistingChunk: true,
            },
            // Lucide icons — separate chunk for caching (shared across pages)
            lucide: {
              test: /[\\/]node_modules[\\/]lucide-react[\\/]/,
              name: 'lucide-icons',
              priority: 12,
              reuseExistingChunk: true,
            },
            // React vendor
            react: {
              test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/,
              name: 'react-vendor',
              priority: 10,
              reuseExistingChunk: true,
            },
            // Common UI utilities
            common: {
              name: 'common-vendor',
              minChunks: 2,
              priority: 5,
              reuseExistingChunk: true,
            },
          },
        };

        config.optimization.usedExports = true;
        config.optimization.sideEffects = true;
      }
    }
    return config;
  },
};

module.exports = withBundleAnalyzer(nextConfig);
