const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup() {
 const nodes = new Map(), blobs = new Map(), pending = new Map();
 const state = {frames: 0, downloads: 0, finalized: false, draws: [], snapshots: [], closed: 0, contexts: [], encoderConfigs: [], rawDraws: []};
 class Element {
  constructor(tag = '') {
   this.tag = tag; this.listeners = new Map(); this.style = {setProperty(name,value) {this[name]=value;}};
   this.classList = {toggle() {}, add() {}, remove() {}};
   this.children = []; this.value = ''; this.checked = false;
   this.readyState = 2; this.duration = 2; this._time = 0;
  }
  addEventListener(name, handler) {if (!this.listeners.has(name)) this.listeners.set(name, new Set());this.listeners.get(name).add(handler);}
  removeEventListener(name, handler) {this.listeners.get(name)?.delete(handler);}
  emit(name) {for (const handler of [...this.listeners.get(name) || []]) handler();}
  set src(url) {
   this.url = url; const file = blobs.get(url); if (!file) return;
   const finish = () => {
    this.naturalWidth = this.videoWidth = 320; this.naturalHeight = this.videoHeight = 240;
    this.emit(this.tag === 'video' ? 'loadeddata' : 'load');
   };
   if (file.pending) pending.set(file.name, finish); else queueMicrotask(finish);
  }
  get src() {return this.url;}
  set currentTime(time) {this._time = time; queueMicrotask(() => this.emit('seeked'));}
  get currentTime() {return this._time;}
  load() {} pause() {} removeAttribute() {} setAttribute() {}
  append(...children) {this.children.push(...children);}
  replaceChildren() {this.children = [];}
  click() {state.downloads++;}
  getContext(type,options) {
   state.contexts.push(options);let clipped=false;
   return {save() {},restore() {clipped=false;},beginPath() {},rect() {},roundRect() {state.rounded=(state.rounded||0)+1;},clip() {clipped=true;},clearRect() {},fillRect() {},
    drawImage: (...args) => {if(typeof args[0]?.close!=='function')assert.equal(clipped,true,'media draw must be clipped');state.rawDraws.push(args);state.draws.push(args.length===9?[args[0],...args.slice(5)]:args);},
    putImageData() {},getImageData: (x,y,width,height) => {state.snapshots.push({width,height});return {data:new Uint8ClampedArray([1,2,3,255])};}
   };
  }
 }
 function get(id) {if (!nodes.has(id)) nodes.set(id, new Element());return nodes.get(id);}
 for (const [id, value] of Object.entries({preset:'1280,720',photoWidth:'180',speed:'240',encodingQuality:'maximum',duration:'5',background:'#101116'})) get(id).value = value;
 get('preventUpscale').checked = true;get('canvas').width = 1280;get('canvas').height = 720;
 class Output {constructor() {this.state = 'pending';} addVideoTrack() {} async start() {} async finalize() {this.state='finalized';state.finalized=true;} async cancel() {this.state='canceled';}}
 const sandbox = {document:{getElementById:get,createElement:tag=>new Element(tag)},Image:class extends Element {constructor() {super('img');}},URL:{createObjectURL:file=>{const url=`blob:${blobs.size}`;blobs.set(url,file);return url;},revokeObjectURL(){}},Blob,VideoEncoder:class {},Mediabunny:{Output,Mp4OutputFormat:class {},WebMOutputFormat:class {},BufferTarget:class {buffer=new ArrayBuffer(8);},VideoSample:class {constructor(data,options) {this.data=data.slice();this.options=options;}close() {state.closed++;}},VideoSampleSource:class {constructor(config) {state.encoderConfigs.push(config);}async add(sample) {assert.equal(sample.options.format,'RGBA');assert.equal(sample.options.layout[0].stride,sample.options.codedWidth*4);assert.equal(sample.options.timestamp,state.frames/60);state.frames++;}},Quality:class {},canEncodeVideo:async()=>true},requestAnimationFrame(){},setTimeout:(fn,ms)=>ms===60000?0:setTimeout(fn,ms===15000?30:ms),clearTimeout};
 vm.createContext(sandbox);vm.runInContext(fs.readFileSync(require.resolve('../app.js'),'utf8'),sandbox);
 return {get,pending,state,run:code=>vm.runInContext(code,sandbox),sandbox};
}
const tick = () => new Promise(resolve=>setImmediate(resolve));

test('drop snapshots FileList, updates count before batch ends, then enables render', async()=>{
 const app=setup();app.sandbox.files=[{name:'photo.png',type:'image/png'},{name:'clip.mp4',type:'video/mp4',pending:true}];
 const upload=app.run('addFiles(files)');app.sandbox.files.length=0;
 await tick();
 assert.equal(app.get('photoCount').textContent,'1 foto · 0 video');
 assert.equal(app.get('export').disabled,true);
 assert.match(app.get('export').innerHTML,/Membaca upload/);
 app.pending.get('clip.mp4')();await upload;
 assert.equal(app.get('photoCount').textContent,'1 foto · 1 video');
 assert.equal(app.get('export').disabled,false);
 await app.run("$('export').onclick()");
 assert.equal(app.state.frames,300);assert.equal(app.state.finalized,true);assert.equal(app.state.downloads,1);
 assert.equal(app.get('export').disabled,false);
});

test('stalled file times out without leaving a valid upload locked', async()=>{
 const app=setup();app.sandbox.files=[{name:'good.png',type:'image/png'},{name:'stalled.png',type:'image/png',pending:true}];
 await app.run('addFiles(files)');
 assert.equal(app.get('photoCount').textContent,'1 foto · 0 video');
 assert.equal(app.get('export').disabled,false);
 assert.match(app.get('status').textContent,/stalled.png/);
 assert.equal(app.run('loadingFiles'),0);
});

test('video upload renders all 300 frames and restores preview time', async()=>{
 const app=setup();app.sandbox.files=[{name:'clip.mp4',type:'video/mp4'}];
 await app.run('addFiles(files)');app.run('photos[0].time=.75');
 await app.run("$('export').onclick()");
 assert.equal(app.state.frames,300);assert.equal(app.state.finalized,true);
 assert.equal(app.run('photos[0].time'),.75);
 assert.equal(app.run('photos[0].img.currentTime'),.75);
});


test('landscape photo keeps its original ratio on a 3:4 canvas in preview and export', async()=>{
 const app=setup();app.sandbox.files=[{name:'landscape.png',type:'image/png'}];
 await app.run('addFiles(files)');
 app.run("photos[0].raster.width=1600;photos[0].raster.height=900;$('preset').value='1080,1440';updateCanvas();");
 assert.equal(app.get('canvas').width,1080);assert.equal(app.get('canvas').height,1440);
 assert.equal(app.get('canvas').style['--canvas-ratio'],.75);
 const preview=app.state.draws.at(-1);
 assert.equal(preview[3],180);assert.equal(preview[4],101.25);
 app.state.draws.length=0;
 await app.run("$('export').onclick()");
 assert.equal(app.state.frames,300);
 for(const draw of app.state.draws){assert.equal(draw[3]/draw[4],16/9);}
});

test('switching landscape and portrait media preserves each source ratio', async()=>{
 const app=setup();app.sandbox.files=[{name:'landscape.png',type:'image/png'},{name:'portrait.mp4',type:'video/mp4'}];
 await app.run('addFiles(files)');
 app.run("photos[0].raster.width=1600;photos[0].raster.height=900;photos[1].img.videoWidth=900;photos[1].img.videoHeight=1600;$('preset').value='1080,1440';updateCanvas();");
 assert.equal(app.run('size().w/size().h'),16/9);
 app.run('x=canvas.width-size().w-1;dx=1;dy=0;advance(1/60);draw();');
 assert.equal(app.run('current'),1);assert.equal(app.run('size().w/size().h'),9/16);
 app.run('x=canvas.width-size().w-1;dx=1;advance(1/60);draw();');
 assert.equal(app.run('current'),0);assert.equal(app.run('size().w/size().h'),16/9);
});

test('switching to a taller media near a corner does not immediately skip it', async()=>{
 const app=setup();app.sandbox.files=[{name:'wide.png',type:'image/png'},{name:'tall.png',type:'image/png'},{name:'next.png',type:'image/png'}];
 await app.run('addFiles(files)');
 app.run("$('preset').value='1080,1440';updateCanvas();photos[0].raster.width=1600;photos[0].raster.height=900;photos[1].img.naturalWidth=900;photos[1].img.naturalHeight=1600;current=0;x=canvas.width-size().w-1;y=1300;dx=1;dy=.2;advance(1/60);");
 assert.equal(app.run('current'),1);
 assert.equal(app.run('bounces'),1);
 app.run('advance(1/60)');
 assert.equal(app.run('current'),1,'new media must not be skipped because its larger height touches the bottom');
 assert.equal(app.run('bounces'),1);
});

test('right-edge media replacement stays attached to the right edge when widths differ', async()=>{
 const app=setup();app.sandbox.files=[{name:'small.png',type:'image/png'},{name:'large.png',type:'image/png'}];
 await app.run('addFiles(files)');
 app.run('photos[1].img.naturalWidth=80;photos[1].img.naturalHeight=80;current=0;x=canvas.width-size().w-1;y=100;dx=1;dy=.2;advance(1/60);');
 assert.equal(app.run('current'),1);
 assert.equal(app.run('x+size().w'),app.get('canvas').width);
});


test('export uses isolated RGBA frames, software raster context, and closes all samples', async()=>{
 const app=setup();app.sandbox.files=[{name:'landscape.png',type:'image/png'}];
 await app.run('addFiles(files)');app.run("$('preset').value='1080,1440';updateCanvas();");
 await app.run("$('export').onclick()");
 assert.equal(app.state.snapshots.length,300);assert.equal(app.state.closed,300);
 assert.ok(app.state.snapshots.every(frame=>frame.width===1080&&frame.height===1440));
 assert.ok(app.state.contexts.every(options=>!options||options.willReadFrequently===true));
 assert.equal(app.state.encoderConfigs[0].hardwareAcceleration,'prefer-software');
});

test('export falls back when software encoding is unavailable', async()=>{
 const app=setup();app.sandbox.files=[{name:'photo.png',type:'image/png'}];
 app.sandbox.Mediabunny.canEncodeVideo=async(codec,options)=>options.hardwareAcceleration!=='prefer-software';
 await app.run('addFiles(files)');await app.run("$('export').onclick()");
 assert.equal(app.state.encoderConfigs[0].hardwareAcceleration,'no-preference');
 assert.equal(app.state.finalized,true);assert.equal(app.state.closed,300);
});

test('samples are released and preview is unlocked on encoder failure', async()=>{
 const app=setup();app.sandbox.files=[{name:'photo.png',type:'image/png'}];
 app.sandbox.Mediabunny.VideoSampleSource=class {async add() {throw Error('encoder failure');}};
 await app.run('addFiles(files)');await app.run("$('export').onclick()");
 assert.equal(app.state.closed,1);assert.equal(app.get('export').disabled,false);
 assert.match(app.get('status').textContent,/encoder failure/);
});

test('bundled Mediabunny accepts the exact RGBA layout used for export',()=>{
 const context=vm.createContext({console,TextEncoder,TextDecoder,setTimeout,clearTimeout});
 vm.runInContext(fs.readFileSync(require.resolve('../vendor/mediabunny.js'),'utf8'),context);
 const result=vm.runInContext(`
  const data=new Uint8Array(1080*4*2);data[0]=123;
  const sample=new Mediabunny.VideoSample(data,{format:'RGBA',codedWidth:1080,codedHeight:2,layout:[{offset:0,stride:1080*4}],timestamp:0,duration:1/60});
  data[0]=0;
  const properties={width:sample.codedWidth,height:sample.codedHeight,format:sample.format,firstByte:sample._data[0]};sample.close();properties;
 `,context);
 assert.equal(result.width,1080);assert.equal(result.height,2);assert.equal(result.format,'RGBA');assert.equal(result.firstByte,123);
});


test('photo drawing uses oriented bitmap dimensions rather than conflicting image metadata', async()=>{
 const app=setup();let closed=false;
 app.sandbox.createImageBitmap=async(file,options)=>{
  assert.equal(options.imageOrientation,'from-image');
  return {width:1600,height:900,close(){closed=true;}};
 };
 app.sandbox.files=[{name:'oriented.jpg',type:'image/jpeg'}];
 await app.run('addFiles(files)');
 // Simulate stale/conflicting HTML image dimensions; drawing must use actual pixels.
 app.run("photos[0].img.naturalWidth=900;photos[0].img.naturalHeight=1600;$('preset').value='1080,1440';updateCanvas();");
 assert.equal(closed,true);assert.equal(app.run('size().w/size().h'),16/9);
 const draw=app.state.rawDraws.at(-1);
 assert.equal(draw[0],app.run('photos[0].raster'));
 assert.deepEqual(draw.slice(1,5),[0,0,1280,720]);
 assert.equal(draw[7]/draw[8],16/9);
 await app.run("$('export').onclick()");
 assert.equal(app.state.finalized,true);
 assert.equal(app.state.rawDraws.at(-1)[7]/app.state.rawDraws.at(-1)[8],16/9);
});

test('photo decode fallback remains usable if bitmap format is unsupported', async()=>{
 const app=setup();app.sandbox.createImageBitmap=async()=>{throw Error('unsupported');};
 app.sandbox.files=[{name:'photo.svg',type:'image/svg+xml'}];await app.run('addFiles(files)');
 assert.equal(app.run('photos.length'),1);assert.equal(app.get('export').disabled,false);
});


test('custom background preserves ratio with cover/contain and is omitted in transparent mode',async()=>{
 const app=setup();app.sandbox.files=[{name:'photo.png',type:'image/png'}];await app.run('addFiles(files)');
 app.sandbox.event={target:{files:[{name:'back.png',type:'image/png'}],value:'back.png'}};
 await app.run("$('backgroundFile').onchange(event)");
 app.get('backgroundFit').value='cover';app.state.rawDraws.length=0;app.run('draw()');
 let background=app.state.rawDraws[0];assert.equal(background[7],1280);assert.equal(background[8],960);assert.equal(background[6],-120);
 app.get('backgroundFit').value='contain';app.state.rawDraws.length=0;app.run('draw()');
 background=app.state.rawDraws[0];assert.equal(background[7],960);assert.equal(background[8],720);assert.equal(background[5],160);
 app.get('transparent').checked=true;app.state.rawDraws.length=0;app.run('updateExportMode()');
 assert.equal(app.state.rawDraws.length,1);assert.equal(app.get('backgroundFile').disabled,true);
 app.get('transparent').checked=false;app.run("$('removeBackground').onclick()");assert.equal(app.run('customBackground'),null);
});

test('video background advances and restores its preview position after export',async()=>{
 const app=setup();app.sandbox.files=[{name:'photo.png',type:'image/png'}];await app.run('addFiles(files)');
 app.sandbox.event={target:{files:[{name:'back.mp4',type:'video/mp4'}],value:''}};
 await app.run("$('backgroundFile').onchange(event)");
 app.run('advance(.25)');assert.equal(app.run('customBackground.time'),.25);
 await app.run("$('export').onclick()");
 assert.equal(app.state.frames,300);assert.equal(app.state.finalized,true);
 assert.equal(app.run('customBackground.time'),.25);assert.equal(app.run('customBackground.img.currentTime'),.25);
 assert.equal(app.get('backgroundFile').disabled,false);
});

test('numeric media width supports values above 640 with original aspect ratio',async()=>{
 const app=setup();app.sandbox.files=[{name:'large.png',type:'image/png'}];await app.run('addFiles(files)');
 app.run("photos[0].raster.width=1600;photos[0].raster.height=800;$('preset').value='1920,1080';updateCanvas();$('photoWidthNumber').value='1200';$('photoWidthNumber').onchange();");
 assert.equal(app.run('size().w'),1200);assert.equal(app.run('size().h'),600);
 assert.equal(app.get('photoWidthNumber').value,1200);
});


test('large photos retain bounded rasters and release original image sources',async()=>{
 const app=setup();let closed=0;
 app.sandbox.createImageBitmap=async()=>({width:6000,height:4000,close(){closed++;}});
 app.sandbox.files=Array.from({length:15},(_,i)=>({name:`photo${i}.jpg`,type:'image/jpeg'}));
 await app.run('addFiles(files)');
 assert.equal(app.run('photos.length'),15);assert.equal(closed,15);
 assert.equal(app.run('photos[0].raster.width'),1280);
 assert.ok(app.run('retainedBytes()')<80*1024*1024);
 assert.ok(app.get('photoList').children.every(item=>item.children[0].tag==='canvas'));
});
test('memory budget rejects another oversized media without losing collection',async()=>{
 const app=setup();app.sandbox.files=[{name:'first.jpg',type:'image/jpeg'}];await app.run('addFiles(files)');
 app.run('photos[0].memoryBytes=MEDIA_BUDGET');
 app.sandbox.files=[{name:'next.jpg',type:'image/jpeg'}];await app.run('addFiles(files)');
 assert.equal(app.run('photos.length'),1);assert.match(app.get('status').textContent,/Memori koleksi penuh/);
});
test('rounded clipping applies in both preview and exported frames',async()=>{
 const app=setup();app.sandbox.files=[{name:'photo.jpg',type:'image/jpeg'}];await app.run('addFiles(files)');
 app.get('cornerRadius').value='60';app.run("$('cornerRadius').oninput()");
 assert.ok(app.state.rounded>0);const before=app.state.rounded;
 await app.run("$('export').onclick()");assert.ok(app.state.rounded>=before+300);
});
