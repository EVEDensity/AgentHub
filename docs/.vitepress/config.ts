import { defineConfig } from 'vitepress';

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: 'AgentHub',
  description: 'Enterprise Self-Hosted Multi-Agent Collaboration Platform',
  lang: 'zh-CN',
  base: '/',
  lastUpdated: true,
  cleanUrls: true,
  ignoreDeadLinks: true,

  head: [
    ['link', { rel: 'icon', href: '/favicon.ico' }],
    ['meta', { name: 'theme-color', content: '#6366f1' }],
  ],

  themeConfig: {
    logo: { light: '/logo-light.svg', dark: '/logo-dark.svg' },
    siteTitle: 'AgentHub Docs',

    nav: [
      { text: '指南', link: '/zh/guide/what-is-agenthub' },
      { text: 'API', link: '/zh/api/overview' },
      { text: '高级', link: '/zh/advanced/architecture' },
      { text: 'GitHub', link: 'https://github.com/EVEDensity/AgentHub' },
    ],

    sidebar: {
      '/zh/guide/': [
        {
          text: '快速开始',
          items: [
            { text: '什么是 AgentHub？', link: '/zh/guide/what-is-agenthub' },
            { text: '5 分钟快速部署', link: '/zh/guide/quick-start' },
            { text: '核心概念', link: '/zh/guide/concepts' },
            { text: '架构总览', link: '/zh/guide/architecture' },
          ],
        },
        {
          text: '基础教程',
          items: [
            { text: '创建第一个 Agent', link: '/zh/guide/create-agent' },
            { text: '构建工作流', link: '/zh/guide/build-workflow' },
            { text: '接入知识库', link: '/zh/guide/knowledge-base' },
            { text: 'AgentNet 多 Agent 协作', link: '/zh/guide/agentnet' },
            { text: 'MCP 协议集成', link: '/zh/guide/mcp-integration' },
          ],
        },
      ],
      '/zh/api/': [
        {
          text: 'API 参考',
          items: [
            { text: '概述', link: '/zh/api/overview' },
            { text: '认证', link: '/zh/api/authentication' },
            { text: 'Agent API', link: '/zh/api/agent' },
            { text: '工作流 API', link: '/zh/api/workflow' },
            { text: '知识库 API', link: '/zh/api/knowledge' },
            { text: 'MCP Gateway API', link: '/zh/api/mcp-gateway' },
            { text: 'A2A Protocol API', link: '/zh/api/a2a' },
            { text: 'WebSocket 事件', link: '/zh/api/websocket' },
          ],
        },
      ],
      '/zh/advanced/': [
        {
          text: '高级主题',
          items: [
            { text: '架构详解', link: '/zh/advanced/architecture' },
            { text: 'ContextOS 4 层记忆', link: '/zh/advanced/contextos' },
            { text: 'AgentNet DAG 编排', link: '/zh/advanced/agentnet-dag' },
            { text: 'Docker 沙箱安全', link: '/zh/advanced/sandbox-security' },
            { text: 'MCP 协议深入', link: '/zh/advanced/mcp-deep-dive' },
            { text: 'A2A 互操作', link: '/zh/advanced/a2a-interop' },
            { text: '性能调优', link: '/zh/advanced/performance' },
            { text: '安全架构', link: '/zh/advanced/security' },
            { text: 'K8s 生产部署', link: '/zh/advanced/k8s-deployment' },
            { text: 'CI/CD 流水线', link: '/zh/advanced/cicd' },
          ],
        },
      ],
    },

    socialLinks: [
      { icon: 'github', link: 'https://github.com/EVEDensity/AgentHub' },
    ],

    editLink: {
      pattern: 'https://github.com/EVEDensity/AgentHub/edit/main/docs/:path',
      text: '在 GitHub 上编辑此页',
    },

    footer: {
      message: '基于 Apache 2.0 协议开源发布',
      copyright: `© ${new Date().getFullYear()} AgentHub Community`,
    },

    search: {
      provider: 'local',
    },
  },

  // i18n: more locales can be added for en, ja, etc.
  locales: {
    root: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
    },
  },
});
