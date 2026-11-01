const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

// A small pixel canvas exercises the full source -> one-pixel strip -> output
// path with known colors, rather than checking only drawImage arguments.
function setup(){
 const nodes=new Map(),downloads=[];
 class Element{
  constructor(){this.value='';this.width=0;this.height=0;this.style={setProperty(){}};this.classList={add(){},remove(){}};this.hidden=false;this.listeners=new Map();}
  addEventListener(name,handler){if(!this.listeners.has(name))this.listeners.set(name,new Set());this.listeners.get(name).add(handler);}
  removeEventListener(name,handler){this.listeners.get(name)?.delete(handler);}
  emit(name){for(const handler of [...this.listeners.get(name)||[]])handler();}
  replaceWith(node){nodes.set(this.id||'edgeVideo',node);}
  click(){downloads.push({filename:this.download,url:this.href});}
 }
 class Canvas extends Element{
  getContext(){
   const canvas=this;
   return {clearRect(){canvas.pixels=new Uint8ClampedArray(canvas.width*canvas.height*4);},
    drawImage(source,sx,sy,sw,sh,dx,dy,dw,dh){
     if(!canvas.pixels||canvas.pixels.length!==canvas.width*canvas.height*4)canvas.pixels=new Uint8ClampedArray(canvas.width*canvas.height*4);
     for(let y=0;y<dh;y++)for(let x=0;x<dw;x++){
      const from=((sy+Math.floor(y*sh/dh))*source.width+sx+Math.floor(x*sw/dw))*4;
      canvas.pixels.set(source.pixels.slice(from,from+4),((dy+y)*canvas.width+dx+x)*4);
     }
    },getImageData(){return {data:canvas.pixels.slice()};}};
  }
  toBlob(callback,type,quality){this.output={pixels:this.pixels.slice(),width:this.width,height:this.height,type,quality};callback(this.output);}
 }
 class Video extends Element{
  constructor(){super();this.videoWidth=this.width=2;this.videoHeight=this.height=2;this.duration=2;this.readyState=2;this.paused=true;this._time=0;this.updatePixels();}
  updatePixels(){this.pixels=new Uint8ClampedArray([this._time>=.5?0:255,0,0,255,0,255,0,255,255,255,255,255,0,0,255,255]);}
  set currentTime(time){this._time=time;this.updatePixels();queueMicrotask(()=>this.emit('seeked'));}
  get currentTime(){return this._time;}
  set src(url){this.url=url;}
  load(){if(this.url)queueMicrotask(()=>this.emit('loadeddata'));}
  removeAttribute(){this.url=null;}
  pause(){this.paused=true;}
  play(){this.paused=false;return Promise.resolve();}
 }
 const get=id=>{if(!nodes.has(id))nodes.set(id,id==='edgeCanvas'?new Canvas():id==='edgeVideo'?new Video():new Element());return nodes.get(id);};
 for(const [id,value]of Object.entries({edgePreset:'1280,720',edgeSide:'left',edgeFormat:'png',edgeQuality:'maximum',edgeDuration:'1'}))get(id).value=value;
 const source={width:2,height:2,pixels:new Uint8ClampedArray([255,0,0,255,0,255,0,255,255,255,255,255,0,0,255,255]),close(){}};
 const context={document:{getElementById:get,createElement:tag=>tag==='canvas'?new Canvas():tag==='video'?new Video():new Element()},createImageBitmap:async()=>source,URL:{createObjectURL:blob=>blob,revokeObjectURL(){}},requestAnimationFrame(){},setTimeout:(fn,ms)=>ms===60000?0:setTimeout(fn,ms),clearTimeout};
 get('edgeVideo').id='edgeVideo';
 vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../edge.js'),'utf8'),context);
 return {get,downloads,context,source};
}
const pixel=(canvas,x,y)=>Array.from(canvas.pixels.slice((y*canvas.width+x)*4,(y*canvas.width+x+1)*4));

test('all four edges expand the exact edge pixels with correct stripe direction',async()=>{
 const app=setup();await app.get('edgeFile').onchange({target:{files:[{name:'colors.png',type:'image/png'}]}});
 const canvas=app.get('edgeCanvas');
 const cases=[['left',[255,0,0,255],[255,255,255,255]],['right',[0,255,0,255],[0,0,255,255]],['top',[255,0,0,255],[0,255,0,255]],['bottom',[255,255,255,255],[0,0,255,255]]];
 for(const [side,first,last]of cases){
  app.get('edgeSide').value=side;app.get('edgeSide').onchange();
  assert.deepEqual(pixel(canvas,0,0),first,side);
  assert.deepEqual(pixel(canvas,canvas.width-1,canvas.height-1),last,side);
  if(side==='left'||side==='right')assert.deepEqual(pixel(canvas,canvas.width-1,0),first);
  else assert.deepEqual(pixel(canvas,0,canvas.height-1),first);
 }
});

test('PNG export uses selected output dimensions and contains only edge colors',async()=>{
 const app=setup();await app.get('edgeFile').onchange({target:{files:[{name:'colors.png',type:'image/png'}]}});
 app.get('edgePreset').value='custom';app.get('edgeWidth').value='241';app.get('edgeHeight').value='319';app.get('edgePreset').onchange();
 await app.get('edgeExport').onclick();
 const result=app.downloads[0];assert.equal(result.filename,'edge-left-242x320.png');
 assert.equal(result.url.type,'image/png');assert.equal(result.url.width,242);assert.equal(result.url.height,320);
 const colors=new Set();for(let i=0;i<result.url.pixels.length;i+=4)colors.add(Array.from(result.url.pixels.slice(i,i+4)).join(','));
 assert.deepEqual([...colors].sort(),['255,0,0,255','255,255,255,255']);
 assert.equal(app.get('edgeExport').disabled,false);
});

test('JPG quality changes compression without changing canvas size',async()=>{
 const app=setup();await app.get('edgeFile').onchange({target:{files:[{name:'colors.png',type:'image/png'}]}});
 app.get('edgeFormat').value='jpeg';app.get('edgeQuality').value='economy';app.get('edgeFormat').onchange();await app.get('edgeExport').onclick();
 assert.equal(app.downloads[0].url.type,'image/jpeg');assert.equal(app.downloads[0].url.quality,.65);
 assert.equal(app.downloads[0].url.width,1280);
});


test('video MP4 exports animated edge pixels at 60 fps and preserves preview position',async()=>{
 const app=setup(),frames=[];let closed=0,finalized=false;
 app.context.VideoEncoder=class {};
 app.context.Mediabunny={
  Quality:class {},canEncodeVideo:async()=>true,Mp4OutputFormat:class {},BufferTarget:class {buffer=new ArrayBuffer(4);},
  Output:class {addVideoTrack(){}async start(){}async finalize(){finalized=true;this.state='finalized';}async cancel(){this.state='canceled';}},
  VideoSample:class {constructor(data,options){this.data=data;this.options=options;}close(){closed++;}},
  VideoSampleSource:class {async add(sample){frames.push({timestamp:sample.options.timestamp,red:sample.data[0]});}}
 };
 app.context.Blob=Blob;
 await app.get('edgeFile').onchange({target:{files:[{name:'clip.mp4',type:'video/mp4'}]}});
 assert.equal(app.get('edgeFormat').value,'mp4');
 app.get('edgePreset').value='custom';app.get('edgeWidth').value=240;app.get('edgeHeight').value=240;app.get('edgePreset').onchange();
 app.get('edgeDuration').value=1;app.get('edgeVideo').currentTime=.75;
 await app.get('edgeExport').onclick();
 assert.equal(frames.length,60);assert.equal(closed,60);assert.equal(finalized,true);
 assert.equal(frames[0].red,255);assert.equal(frames[30].red,0);
 assert.equal(frames[59].timestamp,59/60);assert.equal(app.get('edgeVideo').currentTime,.75);
 assert.equal(app.get('edgeVideo').paused,false);assert.equal(app.get('edgeExport').disabled,false);
 assert.equal(app.downloads[0].filename,'edge-left-240x240-1s.mp4');
});
