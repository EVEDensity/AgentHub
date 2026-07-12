// Test the actual fixArrowLabelWithQuotes logic
const line = `B -- 点击 "新建" --> C[UserForm Modal]`;
const targetRe = /^(.*?)\s*([A-Za-z_][A-Za-z0-9_]*|\[[^\]]*\]|\{[^}]*\}|\([^)]*\)|\(\([^)]*\)\))\s*$/;
const m = line.match(targetRe);
console.log('target match:', m ? 'YES' : 'NO');
if (m) {
  console.log('  head:', JSON.stringify(m[1]));
  console.log('  target:', JSON.stringify(m[2]));

  const head = m[1];
  const a1 = head.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s+--\s+([\s\S]+?)\s+--$/);
  console.log('  a1:', a1 ? 'YES' : 'NO');
  const a2 = head.match(/^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s+--\s+([\s\S]+?)\s+-->$/);
  console.log('  a2:', a2 ? 'YES' : 'NO');
  if (a2) {
    console.log('    label:', JSON.stringify(a2[3]));
    console.log('    includes quote:', a2[3].includes('"'));
  }
}
