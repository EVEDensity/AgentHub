#!/usr/bin/env python3
"""Patch index.tsx: apply all known-safe stubs + remove useMemo."""

with open('frontend/pages/index.tsx.fullbak', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace useEffect import and calls
content = content.replace(
    "import React, { useCallback, useEffect, useMemo, useRef, useState, type JSX } from 'react';",
    "import React, { useCallback, useMemo, useRef, useState, type JSX } from 'react';\nfunction useXffect(_fn, _deps): void {}"
)
content = content.replace('useEffect(', 'useXffect(')

# 2. Stub store hooks
content = content.replace(
    '  const messages = useSessionMessages(sessionId);\n  const isStreaming = useSessionStreaming(sessionId);\n  const { addToast } = useAddToast();',
    '  // STUB: store hooks\n  const messages: Message[] = [];\n  const isStreaming = false;\n  const addToast = () => {};'
)

# 3. Stub useResizableSize
old_rs = (
    "  const [sidebarWidth, setSidebarWidth, resetSidebarWidth] = useResizableSize(\n"
    "    'agenthub.layout.sidebarWidth',\n"
    "    320,\n"
    "    240,\n"
    "    480,\n"
    "  );\n"
    "  // 右侧预览面板宽度：默认 540px，可在 360-960 之间调整\n"
    "  const [previewWidth, setPreviewWidth, resetPreviewWidth] = useResizableSize(\n"
    "    'agenthub.layout.previewWidth',\n"
    "    540,\n"
    "    360,\n"
    "    960,\n"
    "  );"
)
new_rs = (
    "  // STUB: useResizableSize\n"
    "  const sidebarWidth = 320;\n"
    "  const setSidebarWidth = () => {};\n"
    "  const resetSidebarWidth = () => {};\n"
    "  const previewWidth = 540;\n"
    "  const setPreviewWidth = () => {};\n"
    "  const resetPreviewWidth = () => {};"
)
content = content.replace(old_rs, new_rs)

# 4. Stub useFileUpload
old_fu = (
    "  const { handleFileChange, handlePasteFiles, handleRemoveFile } = useFileUpload({\n"
    "    authHeaders, setAttachedFiles, setNotice,\n"
    "  });"
)
new_fu = (
    "  // STUB: useFileUpload\n"
    "  const handleFileChange = () => {};\n"
    "  const handlePasteFiles = () => {};\n"
    "  const handleRemoveFile = () => {};"
)
content = content.replace(old_fu, new_fu)

# 5. Replace useMemo calls
replacements = [
    (
        "  const filteredAgents = useMemo(() => agents.filter((agent) => {\n"
        "    if (selectedRiskLevel === 'all') return true;\n"
        "    return agent.riskLevel === selectedRiskLevel;\n"
        "  }), [agents, selectedRiskLevel]);",
        "  const filteredAgents = FALLBACK_AGENTS;  // STUB: useMemo"
    ),
    (
        "  const filteredWorkflows = useMemo(() => workflows.filter((w) => {\n"
        "    const q = mentionSearch.toLowerCase();\n"
        "    return w.name.toLowerCase().includes(q) || (w.description && w.description.toLowerCase().includes(q));\n"
        "  }), [workflows, mentionSearch]);",
        "  const filteredWorkflows: WorkflowSummary[] = [];  // STUB: useMemo"
    ),
    (
        "  const filteredSkills = useMemo(() => skills.filter((s) => {\n"
        "    const q = mentionSearch.toLowerCase();\n"
        "    return s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);\n"
        "  }), [skills, mentionSearch]);",
        "  const filteredSkills: SkillMeta[] = [];  // STUB: useMemo"
    ),
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f"Replaced useMemo block")
    else:
        print(f"NOT FOUND (first 60 chars): {old[:60]}...")

# 5b. Try other useMemo calls
# sessionName
old_sn = (
    "  const sessionName = useMemo(() => {\n"
    "    if (!sessionId) return '';\n"
    "    if (editingId && editName) return editName;\n"
    "    const s = sessions.find((s) => s.id === sessionId);\n"
    "    return s ? (s.name || s.id || '') : '';\n"
    "  }, [sessions, sessionId, editingId, editName]);"
)
if old_sn in content:
    content = content.replace(old_sn, "  const sessionName = '';  // STUB: useMemo")
    print("Replaced sessionName useMemo")

# currentSession
old_cs = (
    "  const currentSession = useMemo(() => {\n"
    "    return sessions.find((s) => s.id === sessionId) || null;\n"
    "  }, [sessions, sessionId]);"
)
if old_cs in content:
    content = content.replace(old_cs, "  const currentSession = null;  // STUB: useMemo")
    print("Replaced currentSession useMemo")

# percent
old_pct = (
    "  const percent = useMemo(() => {\n"
    "    if (!dag || dag.total === 0) return 0;\n"
    "    return Math.round((dag.completed / dag.total) * 100);\n"
    "  }, [dag]);"
)
if old_pct in content:
    content = content.replace(old_pct, "  const percent = 0;  // STUB: useMemo")
    print("Replaced percent useMemo")

# filteredSessions
old_fs = (
    "  const filteredSessions = useMemo(() => sortSessions(\n"
    "    sessions.filter((s) => {\n"
    "      if (!sessionQuery.trim()) return true;\n"
    "      const q = sessionQuery.toLowerCase();\n"
    "      return (s.name || s.id || '').toLowerCase().includes(q);\n"
    "    })\n"
    "  ), [sessions, sessionQuery]);"
)
if old_fs in content:
    content = content.replace(old_fs, "  const filteredSessions: ChatSession[] = [];  // STUB: useMemo")
    print("Replaced filteredSessions useMemo")

with open('frontend/pages/index.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done patching")
