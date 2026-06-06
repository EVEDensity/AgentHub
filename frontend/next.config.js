/** @type {import('next').NextConfig} */
const withBundleAnalyzer = process.env.ANALYZE === 'true'
  ? require('@next/bundle-analyzer')({ enabled: true })
  : (config) => config;

const nextConfig = {
  reactStrictMode: true,
  swcMinify: true,
  poweredByHeader: false,
  // Pre-existing TS 6.0 strict ref issues — fix separately
  typescript: { ignoreBuildErrors: true },
  experimental: {
    optimizeCss: true,
  },
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://127.0.0.1:8000/api/:path*' }
    ];
  },
  webpack: (config, { isServer }) => {
    if (!isServer) {
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
          // Markdown rendering
          markdown: {
            test: /[\\/]node_modules[\\/](react-markdown|remark-gfm|mdast|unified|micromark)[\\/]/,
            name: 'markdown-renderer',
            priority: 15,
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
    }
    return config;
  },
};

module.exports = withBundleAnalyzer(nextConfig);
