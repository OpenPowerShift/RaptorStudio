// MathJax 3, configured for the AsciiMath that Asciidoctor emits (stem:[...] -> \$...\$), running from the local
// copy in ./mathjax so the formulas page displays with no network.
window.MathJax = {
  loader: { load: ['input/asciimath', 'output/chtml'] },
  asciimath: { delimiters: [['\\$', '\\$']] },
  chtml: { scale: 1.0, mtextInheritFont: true }
};
