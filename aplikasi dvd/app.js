'use strict';
const $ = id => document.getElementById(id);
const canvas = $('canvas'), ctx = canvas.getContext('2d', {alpha:true,willReadFrequently:true});
const photos = [];
let customBackground=null;
let current = 0, x = 0, y = 0, dx = 1, dy = 1, bounces = 0, playing = true, lastTime = 0;
let recording = false, cancelled = false, previewPending = Promise.resolve(), loadingFiles = 0;
function dimensions(media){if(media.raster)return {width:media.raster.width,height:media.raster.height};return media.kind==='video'?{width:media.img.videoWidth,height:media.img.videoHeight}:{width:media.img.naturalWidth,height:media.img.naturalHeight};}
function seekVideo(media){
 if(!media||media.kind!=='video')return Promise.resolve();
 const video=media.img, target=media.time;
 if(Math.abs(video.currentTime-target)<.000001&&video.readyState>=2)return Promise.resolve();
 return new Promise((resolve,reject)=>{
  const cleanup=()=>{clearTimeout(timer);video.removeEventListener('seeked',done);video.removeEventListener('error',fail);};
  const done=()=>{cleanup();resolve();};
  const fail=()=>{cleanup();reject(Error(`Tidak dapat membaca frame video: ${media.name}`));};
  const timer=setTimeout(fail,15000);
  video.addEventListener('seeked',done,{once:true});video.addEventListener('error',fail,{once:true});
  video.currentTime=target;
 });
}
function disposeMedia(media){media.disposed=true;if(media.raster){media.raster.width=0;media.raster.height=0;media.raster=null;}if(media.patch){media.patch.width=0;media.patch.height=0;media.frames=null;media.gif=null;media.restore=null;}if(media.kind==='video'){media.img.pause();media.img.removeAttribute('src');media.img.load();}URL.revokeObjectURL(media.url);}

// Bound retained RGBA memory instead of keeping multiple full-resolution copies.
const PHOTO_EDGE=1280, MEDIA_BUDGET=80*1024*1024;
function retainedBytes(){return [...photos,customBackground].filter(Boolean).reduce((n,m)=>n+(m.memoryBytes||0),0);}
function checkBudget(bytes){if(retainedBytes()+bytes>MEDIA_BUDGET)throw Error('Memori koleksi penuh. Hapus beberapa media atau gunakan file lebih kecil.');}
async function normalizePhoto(file,media){
 let bitmap;
 try{
  if(typeof createImageBitmap==='function'){
   try{bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});}catch{}
  }
  if(media.disposed)return;
  const source=bitmap||media.img;
  const width=bitmap?bitmap.width:media.img.naturalWidth,height=bitmap?bitmap.height:media.img.naturalHeight;
  const scale=Math.min(1,PHOTO_EDGE/Math.max(width,height));
  const rw=Math.max(1,Math.round(width*scale)),rh=Math.max(1,Math.round(height*scale));
  checkBudget(rw*rh*4);
  const raster=document.createElement('canvas');raster.width=rw;raster.height=rh;
  const rasterCtx=raster.getContext('2d',{alpha:true,willReadFrequently:true});
  rasterCtx.save();rasterCtx.beginPath();rasterCtx.rect(0,0,rw,rh);rasterCtx.clip();
  rasterCtx.drawImage(source,0,0,width,height,0,0,rw,rh);rasterCtx.restore();
  media.raster=raster;media.memoryBytes=rw*rh*4;
  media.img.removeAttribute('src');URL.revokeObjectURL(media.url);
 }finally{if(bitmap)bitmap.close();}
}
function mediaSource(media){if(media.kind==='gif')renderGif(media);return media.raster||media.img;}

function size(){
  if (!photos.length) return {w:180,h:120};
  const {width:naturalWidth,height:naturalHeight}=dimensions(photos[current]), desired=Number($('photoWidth').value);
  const scale=Math.min(desired/naturalWidth,canvas.width/naturalWidth,canvas.height/naturalHeight,$('preventUpscale').checked?1:Infinity);
  return {w:naturalWidth*scale,h:naturalHeight*scale};
}
function constrain(){const {w,h}=size();x=Math.max(0,Math.min(x,canvas.width-w));y=Math.max(0,Math.min(y,canvas.height-h));}
function randomize(){const {w,h}=size();x=Math.random()*(canvas.width-w);y=Math.random()*(canvas.height-h);const angle=(25+Math.random()*40)*Math.PI/180;dx=Math.cos(angle)*(Math.random()<.5?-1:1);dy=Math.sin(angle)*(Math.random()<.5?-1:1);bounces=0;$('bounceCount').textContent='0 pantulan';draw();}
function drawFrame(targetCtx,width,height){
 targetCtx.clearRect(0,0,width,height);
 if(!$('transparent').checked){
  targetCtx.fillStyle=$('background').value;targetCtx.fillRect(0,0,width,height);
  if(customBackground){
   const {width:sw,height:sh}=dimensions(customBackground);
   const scale=$('backgroundFit').value==='contain'?Math.min(width/sw,height/sh):Math.max(width/sw,height/sh);
   const dw=sw*scale,dh=sh*scale;
   targetCtx.save();
   try{targetCtx.beginPath();targetCtx.rect(0,0,width,height);targetCtx.clip();targetCtx.drawImage(mediaSource(customBackground),0,0,sw,sh,(width-dw)/2,(height-dh)/2,dw,dh);}
   finally{targetCtx.restore();}
  }
 }
 if(!photos.length)return;
 const {w,h}=size(),scaleX=width/canvas.width,scaleY=height/canvas.height;
 const left=x*scaleX,top=y*scaleY,drawWidth=w*scaleX,drawHeight=h*scaleY;
 targetCtx.save();
 try{
  // Contain sampling at the media boundary, including when a video uses a
  // hardware-backed surface. Never let edge pixels bleed across the canvas.
  targetCtx.beginPath();
  const radius=Math.min(drawWidth,drawHeight)/2*(Number($('cornerRadius').value)||0)/100;
  if(radius>0)targetCtx.roundRect(left,top,drawWidth,drawHeight,radius);
  else targetCtx.rect(left,top,drawWidth,drawHeight);
  targetCtx.clip();
  const media=photos[current],source=mediaSource(media),{width:sourceWidth,height:sourceHeight}=dimensions(media);
  targetCtx.drawImage(source,0,0,sourceWidth,sourceHeight,left,top,drawWidth,drawHeight);
 }finally{targetCtx.restore();}
}
function draw(){drawFrame(ctx,canvas.width,canvas.height);}
function advance(dt){
 if(dt<=0)return;
 if(customBackground&&!$('transparent').checked&&['video','gif'].includes(customBackground.kind))customBackground.time=(customBackground.time+dt)%(customBackground.kind==='gif'?customBackground.gifDuration:customBackground.img.duration);
 if(!photos.length)return;
 const active=photos[current];if(['video','gif'].includes(active.kind))active.time=(active.time+dt)%(active.kind==='gif'?active.gifDuration:active.img.duration);
 const {w,h}=size(),speed=Number($('speed').value);
 x+=dx*speed*dt;y+=dy*speed*dt;
 let edgeX=null,edgeY=null;
 if(w>=canvas.width-.001){x=0;}
 else if(x<=0&&dx<0){x=0;dx=Math.abs(dx);edgeX='left';}
 else if(x+w>=canvas.width&&dx>0){x=canvas.width-w;dx=-Math.abs(dx);edgeX='right';}
 if(h>=canvas.height-.001){y=0;}
 else if(y<=0&&dy<0){y=0;dy=Math.abs(dy);edgeY='top';}
 else if(y+h>=canvas.height&&dy>0){y=canvas.height-h;dy=-Math.abs(dy);edgeY='bottom';}
 if(edgeX||edgeY){
  const centerX=x+w/2,centerY=y+h/2;
  current=(current+1)%photos.length;
  const next=size();
  // Keep the new media on the wall it hit and preserve its center on the
  // other axis. A size change must not count as another collision.
  x=edgeX==='left'?0:edgeX==='right'?canvas.width-next.w:centerX-next.w/2;
  y=edgeY==='top'?0:edgeY==='bottom'?canvas.height-next.h:centerY-next.h/2;
  constrain();
  if(x<=0)dx=Math.abs(dx);else if(x+next.w>=canvas.width)dx=-Math.abs(dx);
  if(y<=0)dy=Math.abs(dy);else if(y+next.h>=canvas.height)dy=-Math.abs(dy);
  bounces++;$('bounceCount').textContent=`${bounces} pantulan`;updateSourceInfo();
 }
}
async function frame(time){
 const dt=lastTime?Math.min((time-lastTime)/1000,.05):0;lastTime=time;
 if(!recording){
  if(playing)advance(dt);
  try{previewPending=Promise.all([seekVideo(photos[current]),!$('transparent').checked?seekVideo(customBackground):Promise.resolve()]);await previewPending;if(!recording)draw();}
  catch(error){setPlaying(false);$('status').textContent=error.message;}
 }
 requestAnimationFrame(frame);
}
function outputSize(){
 if($('preset').value!=='custom'){
  const [width,height]=$('preset').value.split(',').map(Number);return {width,height};
 }
 const values=['canvasWidth','canvasHeight'].map(id=>Math.round(Math.max(240,Math.min(3840,Number($(id).value)||1080))/2)*2);
 return {width:values[0],height:values[1]};
}
function encodingBitrate(){
 const {width,height}=outputSize();
 const factor={economy:.05,standard:.1,high:.2,maximum:.5}[$('encodingQuality').value]||.1;
 return Math.round(Math.max(2000000,Math.min(160000000,width*height*60*factor))/100000)*100000;
}
function updateQuality(){
 const bitrate=encodingBitrate(),duration=Number($('duration').value)||10;
 $('qualityInfo').textContent=$('encodingQuality').value==='maximum'?'Utamakan detail (quantizer 18 jika didukung), dengan bitrate tinggi sebagai cadangan. File bisa lebih besar. Kompresi video tetap dapat mengubah sedikit detail.':`Target ${(bitrate/1000000).toFixed(1)} Mbps · Perkiraan ±${(bitrate*duration/8/1000000).toFixed(1)} MB. Dimensi dan 60 fps tetap sama; ukuran file aktual bisa berbeda.`;
}
function updateSourceInfo(){
 const actual=size();
 const sizeNote=photos.length?`Ukuran tampil media aktif: ${Math.round(actual.w)} × ${Math.round(actual.h)} px. `:'';
 const desired=Number($('photoWidth').value);
 const enlarged=photos.filter(photo=>desired>dimensions(photo).width);
 $('sourceInfo').textContent=sizeNote+(!photos.length?'Foto besar disesuaikan hingga 1280 px untuk menghemat memori perangkat.':$('preventUpscale').checked?'Resolusi kerja foto maksimal 1280 px. Media tidak diperbesar melebihi ukuran kerja.':enlarged.length?`${enlarged.length} media berpotensi diperbesar melewati ukuran asli sehingga bisa tampak buram.`:'Ukuran media masih dalam resolusi sumber.');
}
$('preventUpscale').onchange=()=>{constrain();updateSourceInfo();draw();};
function updateCanvas(){
 if(recording)return;
 const {width,height}=outputSize();
 if($('preset').value==='custom'){$('canvasWidth').value=width;$('canvasHeight').value=height;}
 canvas.width=width;canvas.height=height;canvas.style.aspectRatio=`${width}/${height}`;canvas.style.setProperty('--canvas-ratio',width/height);
 $('customSize').hidden=$('preset').value!=='custom';
 $('dimensions').textContent=`${width} × ${height} px`;
 $('photoWidthNumber').value=$('photoWidth').value;
 updateQuality();updateSourceInfo();randomize();
}
function renderPhotos(){
 const list=$('photoList');list.replaceChildren();
 photos.forEach((photo,i)=>{
  const item=document.createElement('div');item.className='photo-item';
  const thumbnail=document.createElement(photo.raster?'canvas':photo.kind==='video'?'span':'img');
  if(photo.raster){thumbnail.width=72;thumbnail.height=72;const tc=thumbnail.getContext('2d');tc.save();tc.beginPath();tc.rect(0,0,72,72);tc.clip();const d=dimensions(photo),scale=Math.min(72/d.width,72/d.height);tc.drawImage(photo.raster,0,0,d.width,d.height,(72-d.width*scale)/2,(72-d.height*scale)/2,d.width*scale,d.height*scale);tc.restore();}
  else if(photo.kind==='video'){thumbnail.textContent='▶';}else thumbnail.src=photo.url;
  if(photo.kind==='video'){thumbnail.muted=true;thumbnail.preload='metadata';thumbnail.playsInline=true;}else thumbnail.alt=photo.name;
  item.title=`${i+1}. ${photo.kind==='video'?'Video':'Foto'}: ${photo.name}`;
  const number=document.createElement('span');number.className='media-number';number.textContent=i+1;
  const button=document.createElement('button');button.textContent='×';button.title=`Hapus ${photo.name}`;button.disabled=recording;
  button.onclick=()=>{disposeMedia(photo);photos.splice(i,1);current=0;renderPhotos();constrain();};
  item.append(thumbnail,number,button);list.append(item);
 });
 const videos=photos.filter(photo=>photo.kind==='video').length;
 $('photoCount').textContent=`${photos.length-videos} foto · ${videos} video`;
 updateBackgroundControls();
 $('shuffleOrder').disabled=photos.length<2||recording||loadingFiles>0;
 $('emptyState').hidden=photos.length>0;updateSourceInfo();$('export').disabled=!photos.length||recording||loadingFiles>0;
 $('export').innerHTML=loadingFiles&&!recording?'Membaca upload…':recording?$('export').innerHTML:`↓ &nbsp; Ekspor ${$('transparent').checked?'WebM':'MP4'} <span>60 fps</span>`;
}
function shuffleOrder(){
 if(recording||loadingFiles||photos.length<2)return;
 const active=photos[current],previous=photos.slice();
 for(let i=photos.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[photos[i],photos[j]]=[photos[j],photos[i]];}
 // Make each click visibly change the order, even when the random permutation
 // happens to match the previous order. Media objects keep their video times.
 if(photos.every((media,i)=>media===previous[i]))[photos[0],photos[1]]=[photos[1],photos[0]];
 current=photos.indexOf(active);renderPhotos();
 $('status').textContent='Urutan koleksi diacak. Preview dan ekspor mengikuti urutan baru.';
}
$('shuffleOrder').onclick=shuffleOrder;
function loadMedia(file){
 if(file.type==='image/gif'||/\.gif$/i.test(file.name))return loadGif(file);
 const kind=file.type.startsWith('video/')?'video':file.type.startsWith('image/')?'image':null;
 if(!kind)return Promise.reject(Error(`Format tidak didukung: ${file.name}`));
 const url=URL.createObjectURL(file),img=kind==='video'?document.createElement('video'):new Image();
 const media={img,url,name:file.name,kind,time:0};
 return new Promise((resolve,reject)=>{
  const event=kind==='video'?'loadeddata':'load';
  const cleanup=()=>{clearTimeout(timer);img.removeEventListener(event,done);img.removeEventListener('error',fail);};
  let settled=false;
  const fail=(error)=>{if(settled)return;settled=true;cleanup();disposeMedia(media);reject(error instanceof Error?error:Error(`Tidak dapat membaca: ${file.name}`));};
  const done=async()=>{
   if(settled)return;
   const {width,height}=dimensions(media);
   if(!width||!height||(kind==='video'&&(!Number.isFinite(img.duration)||img.duration<=0))){fail();return;}
   try{
    if(kind==='image')await normalizePhoto(file,media);
    else {media.memoryBytes=width*height*8;checkBudget(media.memoryBytes);}
    if(settled)return;
    settled=true;cleanup();resolve(media);
   }catch(error){fail(error);}
  };
  const timer=setTimeout(fail,15000);
  img.addEventListener(event,done);img.addEventListener('error',fail);
  try{
   if(kind==='video'){img.muted=true;img.playsInline=true;img.preload='auto';}
   img.src=url;if(kind==='video')img.load();
  }catch{fail();}
 });
}
async function addFiles(files){
 // A dropped FileList is only readable during the drop event. Keep a stable copy
 // before the first asynchronous read, and refresh the UI after each ready file.
 const batch=Array.from(files);
 if(recording||loadingFiles||!batch.length)return;
 loadingFiles++;renderPhotos();
 const errors=[];let completed=0;
 try{
  for(const file of batch){
   $('status').textContent=`Membaca media ${completed+1} / ${batch.length}…`;
   try{
    const media=await loadMedia(file);
    photos.push(media);renderPhotos();
    if(photos.length===1)randomize();
   }catch(error){errors.push(`${file.name}: ${error.message}`);}
   completed++;
  }
 }finally{
  loadingFiles--;renderPhotos();
  $('status').textContent=errors.length?`Tidak dapat membaca: ${errors.join(', ')}. Gunakan foto atau video yang didukung browser (misalnya MP4/WebM).`:loadingFiles?'Masih membaca upload lainnya…':`${photos.length} media siap diekspor.`;
  $('files').value='';
 }
}
function updateBackgroundControls(){
 const disabled=recording||loadingFiles>0||$('transparent').checked;
 $('backgroundFile').disabled=disabled;$('backgroundFit').disabled=disabled||!customBackground;
 $('removeBackground').hidden=!customBackground;$('removeBackground').disabled=disabled;
 $('backgroundInfo').textContent=$('transparent').checked?'Background sendiri tidak ikut saat mode transparan aktif.':customBackground?`${customBackground.name} · ${customBackground.kind==='video'?'Video mengulang tanpa audio':customBackground.kind==='gif'?'GIF animasi':'Foto'} · Proporsi asli dipertahankan.`:'Upload foto atau video untuk background. Video mengulang tanpa audio.';
}
$('backgroundFile').onchange=async e=>{
 const file=e.target.files[0];if(!file||recording||loadingFiles)return;
 loadingFiles++;renderPhotos();$('status').textContent=`Membaca background ${file.name}…`;
 try{
  const next=await loadMedia(file);
  if(customBackground)disposeMedia(customBackground);
  customBackground=next;$('status').textContent='Background siap untuk preview dan ekspor.';
 }catch(error){$('status').textContent=error.message;}
 finally{loadingFiles--;e.target.value='';renderPhotos();draw();}
};
$('removeBackground').onclick=()=>{if(recording||loadingFiles)return;if(customBackground)disposeMedia(customBackground);customBackground=null;updateBackgroundControls();draw();};
$('backgroundFit').onchange=draw;
$('cornerRadius').oninput=()=>{$('cornerValue').textContent=`${$('cornerRadius').value}%`;draw();};
$('files').onchange=e=>addFiles(e.target.files);
for(const type of ['dragenter','dragover'])$('dropZone').addEventListener(type,e=>{e.preventDefault();$('dropZone').classList.add('drag');});
for(const type of ['dragleave','drop'])$('dropZone').addEventListener(type,e=>{e.preventDefault();$('dropZone').classList.remove('drag');});
$('dropZone').addEventListener('drop',e=>addFiles(e.dataTransfer.files));
$('encodingQuality').onchange=updateQuality;
$('duration').onchange=updateQuality;
$('preset').onchange=updateCanvas;$('canvasWidth').onchange=updateCanvas;$('canvasHeight').onchange=updateCanvas;
$('speed').oninput=()=>{$('speedValue').textContent=`${$('speed').value} px/s`;};
$('photoWidth').oninput=()=>{$('photoWidthNumber').value=$('photoWidth').value;$('widthValue').textContent=`${$('photoWidth').value} px`;constrain();updateSourceInfo();draw();};
$('photoWidthNumber').onchange=()=>{
 const raw=Number($('photoWidthNumber').value),value=Number.isFinite(raw)&&raw>0?Math.round(raw):180;
 $('photoWidth').max=Math.max(3840,value);$('photoWidth').value=value;$('photoWidth').oninput();
};
$('background').oninput=()=>{$('colorValue').textContent=$('background').value.toUpperCase();draw();};
function setPlaying(value){playing=value;$('play').textContent=value?'Ⅱ':'▶';$('play').setAttribute('aria-label',value?'Jeda':'Putar');}
$('play').onclick=()=>setPlaying(!playing);$('restart').onclick=randomize;
function updateExportMode(){
 const transparent=$('transparent').checked;
 canvas.classList.toggle('transparent',transparent);
 $('alphaInfo').hidden=!transparent;updateBackgroundControls();
 $('background').disabled=transparent||recording;
 $('export').innerHTML=`↓ &nbsp; Ekspor ${transparent?'WebM':'MP4'} <span>60 fps</span>`;
 $('exportInfo').textContent=`${transparent?'WebM transparan':'MP4'} · Render frame demi frame pada 60 fps.`;
 draw();
}
$('transparent').onchange=updateExportMode;
function lock(value){for(const id of ['files','preset','encodingQuality','canvasWidth','canvasHeight','speed','photoWidth','photoWidthNumber','preventUpscale','cornerRadius','background','transparent','duration','restart','play'])$(id).disabled=value;$('cancelExport').hidden=!value;renderPhotos();$('background').disabled=value||$('transparent').checked;}
$('export').onclick=async()=>{
 if(!photos.length||recording||loadingFiles>0)return;
 if(typeof VideoEncoder==='undefined'||typeof Mediabunny==='undefined'){
  $('status').textContent='Render video membutuhkan WebCodecs. Buka di Chrome atau Edge terbaru (localhost atau HTTPS jika diperlukan).';return;
 }
 const saved={current,x,y,dx,dy,bounces,playing,times:photos.map(photo=>photo.time),backgroundTime:customBackground?.time};
 recording=true;cancelled=false;lock(true);
 let output;
 try{
  await previewPending;
  const {Output,Mp4OutputFormat,WebMOutputFormat,BufferTarget,VideoSample,VideoSampleSource,Quality,canEncodeVideo}=Mediabunny;
  const transparent=$('transparent').checked,codec=transparent?'vp9':'avc',formatName=transparent?'WebM':'MP4',extension=transparent?'webm':'mp4';
  const {width,height}=outputSize();
  const quality=new Quality($('encodingQuality').value==='maximum'?{quantizer:18,bitrate:encodingBitrate(),bitrateMode:'variable'}:{bitrate:encodingBitrate(),bitrateMode:'variable'});
  const encoderConfig={codec,quality,alpha:transparent?'keep':'discard',latencyMode:'quality',contentHint:'detail',hardwareAcceleration:'prefer-software'};
  const capabilityConfig={width,height,quality,frameRate:60,alpha:encoderConfig.alpha};
  if(!await canEncodeVideo(codec,{...capabilityConfig,hardwareAcceleration:'prefer-software'}))encoderConfig.hardwareAcceleration='no-preference';
  if(!await canEncodeVideo(codec,{...capabilityConfig,hardwareAcceleration:encoderConfig.hardwareAcceleration}))throw Error(transparent?'Browser belum mendukung render VP9 transparan pada ukuran ini. Coba Chrome/Edge terbaru atau ukuran HD.':'Encoder H.264 tidak mendukung ukuran ini. Coba canvas HD atau browser lain.');
  const renderCanvas=document.createElement('canvas');renderCanvas.width=width;renderCanvas.height=height;
  const renderCtx=renderCanvas.getContext('2d',{alpha:transparent,willReadFrequently:true});
  renderCtx.imageSmoothingEnabled=true;renderCtx.imageSmoothingQuality='high';
  const target=new BufferTarget();output=new Output({format:transparent?new WebMOutputFormat():new Mp4OutputFormat(),target});
  const source=new VideoSampleSource(encoderConfig);
  output.addVideoTrack(source,{frameRate:60});await output.start();
  const duration=Number($('duration').value),totalFrames=duration*60;
  let previousMedia=-1;
  current=0;photos.forEach(photo=>photo.time=0);if(customBackground)customBackground.time=0;randomize();
  $('status').textContent=`Merender setiap frame ke ${formatName}. Kecepatan proses tidak memengaruhi timing video.`;
  for(let i=0;i<totalFrames;i++){
   if(cancelled)break;
   if(i>0)advance(1/60);
   await Promise.all([seekVideo(photos[current]),!transparent?seekVideo(customBackground):Promise.resolve()]);
   drawFrame(renderCtx,width,height);
   // Read back a complete, independent RGBA frame with an explicit row stride.
   // The encoder never retains a live canvas/GPU surface while the next frame
   // is drawn. This also makes portrait widths independent of GPU row padding.
   const pixels=renderCtx.getImageData(0,0,width,height);
   const sample=new VideoSample(pixels.data,{format:'RGBA',codedWidth:width,codedHeight:height,layout:[{offset:0,stride:width*4}],timestamp:i/60,duration:1/60});
   try{await source.add(sample,{keyFrame:i%120===0||current!==previousMedia});}
   finally{sample.close();}
   previousMedia=current;
   if(i%12===0||i===totalFrames-1){
    $('export').textContent=`Merender… ${Math.round((i+1)/totalFrames*100)}%`;
    $('status').textContent=`Memproses frame ${i+1} / ${totalFrames} · ${formatName} · 60 fps`;
    await new Promise(resolve=>setTimeout(resolve,0));
   }
  }
  if(cancelled){await output.cancel();$('status').textContent='Ekspor dibatalkan.';return;}
  $('export').textContent=`Menyelesaikan ${formatName}…`;
  await output.finalize();
  if(cancelled){$('status').textContent='Ekspor dibatalkan.';return;}
  const blob=new Blob([target.buffer],{type:`video/${extension}`} );
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=`bounce-${width}x${height}-${duration}s.${extension}`;link.click();
  setTimeout(()=>URL.revokeObjectURL(url),60000);
  $('status').textContent=`${formatName} selesai: ${totalFrames} frame, 60 fps, ${duration} detik · ${width} × ${height} px.`;
 }catch(error){
  if(output&&output.state!=='finalized'&&output.state!=='canceled'){try{await output.cancel();}catch{}}
  $('status').textContent=`Render gagal: ${error.message}`;
 }finally{
  ({current,x,y,dx,dy,bounces}=saved);photos.forEach((photo,i)=>photo.time=saved.times[i]);
  if(customBackground)customBackground.time=saved.backgroundTime;
  try{await Promise.all([seekVideo(photos[current]),seekVideo(customBackground)]);}catch(error){$('status').textContent=error.message;}
  setPlaying(saved.playing);lastTime=0;recording=false;lock(false);
  $('bounceCount').textContent=`${bounces} pantulan`;
  updateExportMode();
 }
};
$('cancelExport').onclick=()=>{cancelled=true;$('status').textContent='Membatalkan render…';};
updateCanvas();updateExportMode();renderPhotos();requestAnimationFrame(frame);
