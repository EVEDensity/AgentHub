"""Extract all function definitions and useCallback hooks from full file,
merge them into the working step3 structure."""

with open('frontend/pages/index.tsx.fullbak', 'r', encoding='utf-8') as f:
    full = f.read()

with open('frontend/pages/index.tsx.step3', 'r', encoding='utf-8') as f:
    step3 = f.read()

# Find the AuthForm-only render section in step3 and replace it
# We want to keep step3's structure but add full file's functions/callbacks

# In step3, find the part before the render return and after the useRef hooks
# We'll insert all function defs + useCallback hooks from full file there

# Find insertion point in step3: right before "const handleAuthSubmit"
insert_marker = "  // ★ STUB: All useCallback and complex logic\n  const handleAuthSubmit"
if insert_marker not in step3:
    print("ERROR: Cannot find insertion point in step3")
    exit(1)

# Extract all code from full file between useRef section end and render section
# useRef section ends roughly at "const scrollRafRef" + "const lastScrollTimeRef"
# Let's find the end of useRef hooks in the full file
ref_end_marker = "  const scrollRafRef = useRef<number>(0);"
if ref_end_marker not in full:
    print("ERROR: Cannot find ref end in full file")
    exit(1)

ref_end_pos = full.find(ref_end_marker) + len(ref_end_marker)
# Skip past the next line too (lastScrollTimeRef)
next_nl = full.find('\n', ref_end_pos)
ref_end_pos = next_nl + 1

# Render section starts at
render_start = full.find("  // ── Render ──")
if render_start == -1:
    print("ERROR: Cannot find render section")
    exit(1)

# Extract the code between ref end and render start
# But we need to REMOVE:
# - useEffect calls (now useXffect) - keep as comments
# - useSessionMessages, useSessionStreaming, useAddToast - already stubbed in step3
# - useResizableSize - already stubbed in step3
# - useFileUpload - already stubbed in step3
#
# Actually, we're ADDING this to step3 which already has all stubs.
# So we just need: function definitions, useMemo calls, useCallback hooks.
# But NOT: useEffect calls, store hooks.

# Simpler approach: take the middle section and just remove the useEffect blocks
middle = full[ref_end_pos:render_start]

# Replace useEffect calls (which are now useXffect) with comments
# Actually, the full backup still has useEffect. Let's just use the middle section as-is
# and replace problematic hooks

# Remove lines that call useSessionMessages, useSessionStreaming, useAddToast
# (these are the store hooks that are already stubbed in step3)

lines = middle.split('\n')
filtered_lines = []
skip_until_close = False
skip_count = 0

for i, line in enumerate(lines):
    # Skip store hook calls (already handled)
    if 'useSessionMessages(sessionId)' in line or 'useSessionStreaming(sessionId)' in line or 'useAddToast()' in line:
        continue

    # Skip useResizableSize calls (already handled)
    if "useResizableSize(" in line:
        skip_until_close = True
        skip_count = 0
        continue

    if skip_until_close:
        skip_count += 1
        if ');' in line:
            skip_until_close = False
        continue

    # Skip useFileUpload (already handled)
    if "useFileUpload({" in line:
        skip_until_close = True
        skip_count = 0
        continue

    filtered_lines.append(line)

middle_filtered = '\n'.join(filtered_lines)

# Build the new file
# step3 up to the insertion marker
insert_pos = step3.find(insert_marker)
before = step3[:insert_pos]
after = step3[insert_pos:]

new_content = before + '\n  // ── Added from full file ──\n' + middle_filtered + '\n  // ── End added section ──\n\n' + after

with open('frontend/pages/index.tsx', 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f"Done. New file size: {len(new_content)} chars")
print(f"Middle section size: {len(middle_filtered)} chars")
