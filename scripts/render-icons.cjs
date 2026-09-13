// Build-time only: NODE_PATH=<folder>/node_modules node scripts/render-icons.cjs
// Renderer: @resvg/resvg-js 2.6.2. Runtime uses the committed PNG assets.
const { Resvg } = require('@resvg/resvg-js');
const fs = require('fs');
const path = require('path');
const assets = path.join(__dirname, '..', 'chemcompute', 'assets');
const brand = fs.readFileSync(path.join(assets, 'app-icon.svg'), 'utf8');
for (const [size, name] of [[64, 'app-logo.png'], [256, 'app-icon.png']]) {
  fs.writeFileSync(path.join(assets, name), new Resvg(brand, { fitTo: { mode: 'width', value: size } }).render().asPng());
}
for (const file of fs.readdirSync(path.join(assets, 'lucide')).filter(f => f.endsWith('.svg'))) {
  const source = fs.readFileSync(path.join(assets, 'lucide', file), 'utf8');
  for (const [color, suffix] of [['#3475dd', 'blue'], ['#ffffff', 'white']]) {
    const svg = source.replaceAll('currentColor', color);
    fs.writeFileSync(path.join(assets, `icon-${file.slice(0, -4)}-${suffix}.png`), new Resvg(svg, { fitTo: { mode: 'width', value: 22 } }).render().asPng());
  }
}
