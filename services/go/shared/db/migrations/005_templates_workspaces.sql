-- 005: Agent Templates + Workspaces
-- Sprint F2/F4 — template marketplace and workspace isolation.

-- ── Workspaces ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_workspaces (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    owner_id     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_workspaces_tenant ON platform_workspaces(tenant_id);

CREATE TABLE IF NOT EXISTS platform_workspace_members (
    workspace_id TEXT NOT NULL REFERENCES platform_workspaces(id) ON DELETE CASCADE,
    tenant_id    TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'editor',
    invited_by   TEXT NOT NULL DEFAULT '',
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_platform_ws_members_user ON platform_workspace_members(user_id);

-- ── Agent Templates ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_agent_templates (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT 'general',
    icon            TEXT NOT NULL DEFAULT 'smart_toy',
    tags            TEXT[] DEFAULT '{}',
    source          TEXT NOT NULL DEFAULT 'user',
    version         TEXT NOT NULL DEFAULT '1.0',
    author          TEXT NOT NULL DEFAULT '',

    -- Template content (JSON strings for flexibility)
    workflow_json   TEXT NOT NULL DEFAULT '[]',
    prompt_json     TEXT NOT NULL DEFAULT '{}',
    tools_json      TEXT[] DEFAULT '{}',
    knowledge_json  TEXT NOT NULL DEFAULT '{}',
    agent_config    TEXT NOT NULL DEFAULT '{}',

    usage_count     INTEGER NOT NULL DEFAULT 0,
    rating          REAL NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_templates_tenant  ON platform_agent_templates(tenant_id);
CREATE INDEX IF NOT EXISTS idx_platform_templates_source  ON platform_agent_templates(source);
CREATE INDEX IF NOT EXISTS idx_platform_templates_category ON platform_agent_templates(category);

-- ── Seed 15 preset templates ─────────────────────────────────────────
INSERT INTO platform_agent_templates (id, tenant_id, name, description, category, icon, tags, source, agent_config, prompt_json) VALUES
('tpl-customer-service',   '', 'Customer Service',   'Multi-turn support with FAQ knowledge base', 'customer_service', 'support_agent', ARRAY['support','faq','chat'],          'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a helpful customer support agent. Use the knowledge base to answer questions accurately.\n\nKnowledge: {{knowledge_snippets}}\n\nCurrent time: {{current_time}}\n\nUser: {{user_name}}"}'),
('tpl-code-review',         '', 'Code Review',        'Pull request review with bug and security analysis', 'devops', 'code', ARRAY['code','review','git'],          'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a senior code reviewer. Review code for bugs, security vulnerabilities, style issues, and performance problems.\n\nFiles to review:\n{{files}}\n\nBe thorough and provide actionable feedback."}'),
('tpl-data-analysis',       '', 'Data Analysis',      'Explore datasets and produce charts', 'data', 'bar_chart', ARRAY['data','sql','chart'],            'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a data analyst. Analyze the provided data and create meaningful visualizations and insights.\n\nSchema: {{schema}}\n\nFocus on actionable insights."}'),
('tpl-document-search',     '', 'Document Search',    'RAG-powered search over uploaded documents', 'knowledge', 'description', ARRAY['rag','search','document'],       'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"Answer questions based on the provided document knowledge base. Cite specific sources in your responses.\n\nDocuments: {{knowledge_snippets}}\n\nIf the documents do not contain the answer, say so clearly."}'),
('tpl-api-builder',         '', 'API Builder',        'Design, document and test REST APIs', 'api', 'api', ARRAY['api','rest','openapi'],          'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are an API architect. Help design, document, and implement REST APIs following best practices.\n\nSpecification: {{spec}}\n\nUse OpenAPI 3.0 standards."}'),
('tpl-test-generator',      '', 'Test Generator',     'Generate unit, integration and e2e tests', 'devops', 'bug_report', ARRAY['test','unit-test','coverage'],   'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"Generate comprehensive test suites for the given code.\n\nFramework: {{framework}}\n\nCoverage target: {{coverage_target}}\n\nInclude edge cases, error paths, and happy paths."}'),
('tpl-database-admin',      '', 'Database Admin',     'Query, migrate and optimize databases', 'devops', 'database', ARRAY['database','sql','migration'],    'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a database administrator. Help write queries, plan migrations, and optimize performance.\n\nEngine: {{engine}}\n\nAlways consider indexing and query plan efficiency."}'),
('tpl-translator',          '', 'Translator',         'Multi-language translation with context awareness', 'content', 'translate', ARRAY['translate','i18n','language'],   'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a professional translator. Translate content accurately while preserving tone and context.\n\nSource language: {{source_lang}}\n\nTarget language: {{target_lang}}"}'),
('tpl-content-writer',      '', 'Content Writer',     'Blog posts, documentation, marketing copy', 'content', 'edit_note', ARRAY['writing','blog','marketing'],    'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a skilled content writer. Write engaging, well-structured content.\n\nStyle: {{style}}\n\nAudience: {{audience}}\n\nTone: {{tone}}"}'),
('tpl-project-planner',     '', 'Project Planner',    'Task breakdown, estimation and scheduling', 'productivity', 'task_alt', ARRAY['planning','project','agile'],    'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a project manager. Break down projects into actionable tasks with estimates and dependencies.\n\nTimeline: {{timeline}}\n\nMethodology: {{methodology}}\n\nTeam size: {{team_size}}"}'),
('tpl-security-auditor',    '', 'Security Auditor',   'Vulnerability scanning and security reports', 'security', 'shield', ARRAY['security','audit','vulnerability'],'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a security auditor. Review systems, code, and configurations for security vulnerabilities.\n\nScope: {{scope}}\n\nCompliance framework: {{compliance}}\n\nPrioritize findings by severity."}'),
('tpl-onboarding-buddy',    '', 'Onboarding Buddy',   'New hire guidance and FAQ assistant', 'hr', 'person_add', ARRAY['onboarding','hr','guide'],       'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are an onboarding buddy. Help new team members get up to speed with the company, tools, and processes.\n\nRole: {{role}}\n\nTeam: {{team}}\n\nBe friendly and encouraging."}'),
('tpl-legal-review',        '', 'Legal Review',       'Contract and policy compliance analysis', 'legal', 'gavel', ARRAY['legal','contract','compliance'],  'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a legal review assistant. Analyze contracts and policies for key clauses, risks, and compliance issues.\n\nJurisdiction: {{jurisdiction}}\n\nDocument type: {{doc_type}}\n\nAlways note that this is not legal advice."}'),
('tpl-cloud-architect',     '', 'Cloud Architect',    'Infrastructure design and IaC generation', 'devops', 'cloud', ARRAY['cloud','aws','terraform'],       'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a cloud architect. Design scalable, secure, and cost-effective cloud infrastructure.\n\nProvider: {{provider}}\n\nIaC Tool: {{tool}}\n\nEnvironment: {{environment}}"}'),
('tpl-personal-tutor',      '', 'Personal Tutor',     'Interactive learning assistant for any subject', 'education', 'school', ARRAY['education','learning','tutor'],  'builtin', '{"adapterType":"deepseek","baseModelName":"deepseek-chat"}', '{"system":"You are a patient and engaging tutor. Teach concepts interactively at the right level.\n\nSubject: {{subject}}\n\nLevel: {{level}}\n\nUse examples and ask questions to check understanding."}')
ON CONFLICT (id) DO NOTHING;

-- Seed Default workspace
INSERT INTO platform_workspaces (id, tenant_id, name, description, owner_id) VALUES
    ('ws-default', '', 'Default', 'Default workspace for all existing resources', 'system')
ON CONFLICT (id) DO NOTHING;
