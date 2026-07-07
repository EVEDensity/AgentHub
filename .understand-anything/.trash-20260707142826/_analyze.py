import json
import sys
from collections import Counter

with open(r'D:\Users\xyn\Desktop\agenthub\AgenthubV1.2\.understand-anything\intermediate\assembled-graph.json', 'r', encoding='utf-8') as f:
    graph = json.load(f)

with open(r'D:\Users\xyn\Desktop\agenthub\AgenthubV1.2\.understand-anything\intermediate\scan-result.json', 'r', encoding='utf-8') as f:
    scan = json.load(f)

nodes = graph['nodes']
edges = graph.get('edges', [])

# === 1. Scan inventory comparison ===
scan_files = set(f['path'] for f in scan['files'])
scan_total = scan['totalFiles']
print(f'Scan totalFiles field: {scan_total}')
print(f'Scan actual file entries in array: {len(scan["files"])}')

graph_file_ids = set()
graph_file_nodes = []
for n in nodes:
    if n['type'] == 'file':
        graph_file_nodes.append(n)

file_prefixed = set()
for n in nodes:
    if n['id'].startswith('file:'):
        file_prefixed.add(n['id'][5:])

print(f'\nGraph file: nodes: {len(graph_file_nodes)}')
print(f'Graph file: prefix nodes: {len(file_prefixed)}')

# Files in scan but NOT in graph
missing_from_graph = scan_files - file_prefixed
real_missing = sorted([p for p in missing_from_graph if not p.startswith('false/')])
print(f'\nFiles in scan but MISSING from graph (non-false): {len(real_missing)}')
if real_missing:
    for p in real_missing:
        print(f'  MISSING: {p}')

# Files in graph but NOT in scan
extra_in_graph = sorted(file_prefixed - scan_files)
print(f'\nFiles in graph but NOT in scan: {len(extra_in_graph)}')
if extra_in_graph:
    for p in extra_in_graph[:10]:
        print(f'  EXTRA: {p}')

# === 2. Node field completeness ===
print('\n--- FIELD COMPLETENESS ---')
missing_id = [n for n in nodes if 'id' not in n]
missing_type = [n for n in nodes if 'type' not in n]
missing_summary = [n for n in nodes if 'summary' not in n]
missing_complexity = [n for n in nodes if 'complexity' not in n]

file_nodes_no_fp = [n for n in graph_file_nodes if 'filePath' not in n]
sym_nodes = [n for n in nodes if n.get('type') in ('function','class')]
sym_no_fp = [n for n in sym_nodes if 'filePath' not in n]

nofilepath_nodes = [n for n in nodes if '__nofilepath__' in n.get('id','')]

print(f'Nodes missing id: {len(missing_id)}')
print(f'Nodes missing type: {len(missing_type)}')
print(f'Nodes missing summary: {len(missing_summary)}')
print(f'Nodes missing complexity: {len(missing_complexity)}')
print(f'File nodes missing filePath: {len(file_nodes_no_fp)}')
print(f'Symbol nodes missing filePath: {len(sym_no_fp)}')
print(f'__nofilepath__ placeholder nodes: {len(nofilepath_nodes)}')

# Duplicate IDs
id_counts = Counter(n['id'] for n in nodes if 'id' in n)
dups = {k:v for k,v in id_counts.items() if v > 1}
print(f'Duplicate node IDs: {len(dups)}')
if dups:
    for d in list(dups.keys())[:5]:
        print(f'  DUPLICATE: {d} (count={dups[d]})')

# Old vs new style file nodes
old_style = [n for n in graph_file_nodes if 'label' not in n]
new_style = [n for n in graph_file_nodes if 'label' in n]
print(f'\nFile nodes old-style (no label/description): {len(old_style)}')
print(f'File nodes new-style (with label/description): {len(new_style)}')

# === 3. Edge consistency ===
print('\n--- EDGE CONSISTENCY ---')
print(f'Total edges: {len(edges)}')
if edges:
    all_ids = set(n['id'] for n in nodes if 'id' in n)
    missing_sources = set()
    missing_targets = set()
    bad_types = 0
    bad_src = 0
    bad_tgt = 0
    for e in edges:
        if 'type' not in e: bad_types += 1
        if 'source' not in e: bad_src += 1
        elif e['source'] not in all_ids: missing_sources.add(e['source'])
        if 'target' not in e: bad_tgt += 1
        elif e['target'] not in all_ids: missing_targets.add(e['target'])
    
    print(f'Edges missing type: {bad_types}')
    print(f'Edges missing source: {bad_src}')
    print(f'Edges missing target: {bad_tgt}')
    print(f'Edges with missing source nodes: {len(missing_sources)} unique')
    print(f'Edges with missing target nodes: {len(missing_targets)} unique')
    if missing_sources:
        for s in sorted(missing_sources)[:3]:
            print(f'  MISSING SOURCE: {s}')
    if missing_targets:
        for t in sorted(missing_targets)[:3]:
            print(f'  MISSING TARGET: {t}')

# === 4. Stats ===
node_type_counts = Counter(n.get('type') for n in nodes if 'type' in n)
edge_type_counts = Counter(e.get('type') for e in edges if 'type' in e)

print('\n=== FINAL STATS ===')
print(f'Total nodes: {len(nodes)}')
print(f'Total edges: {len(edges)}')
print(f'Node types: {json.dumps(dict(node_type_counts.most_common()), indent=2)}')
print(f'Edge types: {json.dumps(dict(edge_type_counts.most_common()), indent=2)}')
