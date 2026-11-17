// 构建入口（稳定路径）：真正的模板目录与文件名全部来自 config 的 build 段，
// 所以实例轨（runs/xxx-app/）与模板轨（app/）都能用同一条命令：
//   npm run build   /   node tools/build.js
'use strict';
const fs = require('fs');
const path = require('path');

(function main(){
  // 向上找项目根：第一个同时有 config/ 与 server.js 的目录
  let root = __dirname;
  for(let i = 0; i < 6; i++){
    if(fs.existsSync(path.join(root, 'config')) && fs.existsSync(path.join(root, 'server.js'))) break;
    const up = path.dirname(root);
    if(up === root) throw new Error('找不到项目根（需要同时存在 config/ 与 server.js）');
    root = up;
  }

  let app = null;
  for(const f of ['config/local/app.json', 'config/app.example.json']){
    try { app = JSON.parse(fs.readFileSync(path.join(root, f), 'utf8')); } catch(e){}
    if(app) break;
  }
  if(!app) throw new Error('缺少 app 配置（config/local/app.json 或 config/app.example.json）');

  const build = app.build || {};
  if(!build.template_dir) throw new Error('配置缺少 build.template_dir，无法定位构建脚本');
  const script = path.join(root, build.template_dir, 'build.js');
  if(!fs.existsSync(script)) throw new Error('找不到构建脚本：' + path.relative(root, script));

  require(script);
})();
