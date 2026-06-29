const { chromium } = require("playwright-core");
const path=require("path"),fs=require("fs");
const DECK="/Users/keith/dev/gauntlet/cookbook/agent-memory-harness/presentation/index.html";
const OUT="/Users/keith/dev/gauntlet/cookbook/agent-memory-harness/presentation/media/video/build";
function fc(){const b=path.join(process.env.HOME,"Library/Caches/ms-playwright");const d=fs.readdirSync(b).filter(x=>x.startsWith("chromium-")).sort();return path.join(b,d[d.length-1],"chrome-mac-arm64","Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing");}
(async()=>{
const br=await chromium.launch({executablePath:fc(),headless:true});
const pg=await br.newPage({viewport:{width:1440,height:810}});
pg.on("pageerror",e=>console.log("[ERR]",e.message));
await pg.goto("file://"+DECK+"#2");
await pg.waitForTimeout(2500); // let iframe load + animate a bit
await pg.screenshot({path:path.join(OUT,"deck-slide2.png")});
console.log("saved deck-slide2.png");
await br.close();
})();
