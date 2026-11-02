const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function setup(){
 const calls=[];
 const context={console,Uint8Array,Uint8ClampedArray,ImageData:class{constructor(data,width,height){Object.assign(this,{data,width,height});}},checkBudget(){},disposeMedia(){},document:{createElement(){return {width:0,height:0,getContext(){return {clearRect(){calls.push('clear');},putImageData(data){calls.push(Array.from(data.data.slice(0,4)));},getImageData(){return {};},drawImage(){calls.push('draw');}};}};}}};
 vm.createContext(context);
 for(const file of ['vendor/gifuct.js','gif.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),context);
 const bytes=fs.readFileSync(path.join(__dirname,'fixtures/animated.gif'));
 context.file={name:'animated.gif',size:bytes.length,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};
 return {context,calls,run:code=>vm.runInContext(code,context)};
}
test('real animated GIF decodes with correct frame delays and loops after seeking backwards',async()=>{
 const a=setup();await a.run('loadGif(file).then(value=>globalThis.media=value)');
 assert.equal(a.run('media.gifDuration'),.6);
 assert.deepEqual(a.calls.find(Array.isArray),[255,0,0,255]);
 a.calls.length=0;a.run('media.time=.15;renderGif(media)');assert.deepEqual(a.calls.find(Array.isArray),[0,128,0,255]);assert.ok(a.calls.includes('clear'));
 a.calls.length=0;a.run('media.time=.4;renderGif(media)');assert.deepEqual(a.calls.find(Array.isArray),[0,0,255,255]);
 a.calls.length=0;a.run('media.time=.65;renderGif(media)');assert.deepEqual(a.calls.find(Array.isArray),[255,0,0,255]);
});
test('GIF rejects excessive dimensions before parsing or allocating frame surfaces',async()=>{
 const a=setup();a.context.file={name:'huge.gif',size:13,arrayBuffer:async()=>Uint8Array.from([71,73,70,56,57,97,255,255,255,255,0,0,0]).buffer};
 await assert.rejects(a.run('loadGif(file)'),/Resolusi GIF terlalu besar/);
});
