'use strict';
(() => {
 const el=id=>document.getElementById(id);
 const preview=el('edgeCanvas'),strip=document.createElement('canvas');
 const previewCtx=preview.getContext('2d',{willReadFrequently:true});
 let media=null,busy=false,loading=false,cancelled=false;
 const status=message=>{el('edgeStatus').textContent=message;};
 const sourceSize=source=>({width:source.videoWidth||source.naturalWidth||source.width,height:source.videoHeight||source.naturalHeight||source.height});
 function outputSize(){
  if(el('edgePreset').value!=='custom'){const [width,height]=el('edgePreset').value.split(',').map(Number);return {width,height};}
  const [width,height]=['edgeWidth','edgeHeight'].map(id=>Math.round(Math.max(240,Math.min(3840,Number(el(id).value)||1080))/2)*2);
  return {width,height};
 }
 function draw(target,source,width,height,side=el('edgeSide').value){
  const size=sourceSize(source);if(!size.width||!size.height)return;
  const verticalEdge=side==='left'||side==='right';
  strip.width=verticalEdge?1:size.width;strip.height=verticalEdge?size.height:1;
  const stripCtx=strip.getContext('2d',{willReadFrequently:true});
  stripCtx.imageSmoothingEnabled=false;
  stripCtx.drawImage(source,side==='right'?size.width-1:0,side==='bottom'?size.height-1:0,strip.width,strip.height,0,0,strip.width,strip.height);
  target.clearRect(0,0,width,height);
  target.imageSmoothingEnabled=false;
  target.drawImage(strip,0,0,strip.width,strip.height,0,0,width,height);
 }
 function drawPreview(){if(media&&!busy)draw(previewCtx,media.source,preview.width,preview.height);}
 function update(){
  const {width,height}=outputSize(),scale=Math.min(1,960/width,720/height);
  preview.width=Math.round(width*scale);preview.height=Math.round(height*scale);
  preview.style.setProperty('--canvas-ratio',width/height);preview.style.aspectRatio=`${width}/${height}`;
  el('edgeDimensions').textContent=`${width} × ${height} px`;
  const custom=el('edgePreset').value==='custom';el('edgeCustom').hidden=!custom;
  if(custom){el('edgeWidth').value=width;el('edgeHeight').value=height;}
  const side=el('edgeSide').value;
  el('edgeSideInfo').textContent=side==='left'||side==='right'?`Kolom piksel paling ${side==='left'?'kiri':'kanan'} dibentangkan ke seluruh lebar canvas menjadi garis horizontal.`:`Baris piksel paling ${side==='top'?'atas':'bawah'} dibentangkan ke seluruh tinggi canvas menjadi garis vertikal.`;
  const format=el('edgeFormat').value;
  el('edgeDurationField').hidden=format!=='mp4';el('edgeQuality').disabled=busy||format==='png';
  el('edgeQualityInfo').textContent=format==='png'?'PNG selalu menyimpan detail piksel tanpa kompresi lossy.':format==='jpeg'?'Kualitas mengatur kompresi JPG; ukuran canvas tetap sama.':'Kualitas mengatur bitrate video; ukuran canvas dan 60 fps tetap sama.';
  el('edgeExport').textContent=`↓ Ekspor ${format==='jpeg'?'JPG':format.toUpperCase()}`;
  el('edgeExport').disabled=!media||busy||loading;drawPreview();
 }
 function waitFor(target,event,action){
  return new Promise((resolve,reject)=>{
   const clean=()=>{clearTimeout(timer);target.removeEventListener(event,done);target.removeEventListener('error',fail);};
   const done=()=>{clean();resolve();};const fail=()=>{clean();reject(Error('Media tidak dapat dibaca. Coba JPG, PNG, MP4 atau WebM yang didukung browser.'));};
   const timer=setTimeout(fail,15000);target.addEventListener(event,done,{once:true});target.addEventListener('error',fail,{once:true});
   try{action();}catch(error){clean();reject(error);}
  });
 }
 function release(item){
  if(!item)return;
  if(item.kind==='video'){item.source.pause();item.source.removeAttribute('src');item.source.load();}
  else item.source.close?.();
  URL.revokeObjectURL(item.url);
 }
 function lock(value){
  busy=value;
  for(const id of ['edgeFile','edgeSide','edgePreset','edgeWidth','edgeHeight','edgeFormat','edgeQuality','edgeDuration'])el(id).disabled=value;
  el('edgeVideo').controls=!value;el('edgeExport').disabled=value||!media||loading;
  el('edgeCancel').hidden=!value;
  if(!value)update();
 }
 async function load(file){
  if(busy||loading||!file)return;
  loading=true;update();status(`Membaca ${file.name}…`);
  let candidate;
  try{
   const kind=file.type.startsWith('video/')?'video':file.type.startsWith('image/')?'image':null;
   if(!kind)throw Error('Pilih foto atau video.');
   candidate={kind,url:URL.createObjectURL(file),source:null,name:file.name};
   if(kind==='video'){
    const video=document.createElement('video');candidate.source=video;video.muted=true;video.playsInline=true;video.preload='auto';video.loop=true;
    await waitFor(video,'loadeddata',()=>{video.src=candidate.url;video.load();});
    if(!Number.isFinite(video.duration)||video.duration<=0)throw Error('Durasi video tidak valid.');
   }else{
    if(typeof createImageBitmap==='function'){
     try{candidate.source=await createImageBitmap(file,{imageOrientation:'from-image'});}catch{}
    }
    if(!candidate.source){const image=new Image();candidate.source=image;await waitFor(image,'load',()=>{image.src=candidate.url;});}
   }
   const {width,height}=sourceSize(candidate.source);if(!width||!height)throw Error('Ukuran media tidak valid.');
   release(media);media=candidate;candidate=null;
   const oldVideo=el('edgeVideo');oldVideo.pause();oldVideo.removeAttribute('src');oldVideo.load();
   oldVideo.hidden=kind!=='video';el('edgeImage').hidden=kind!=='image';
   if(kind==='video'){
    media.source.id='edgeVideo';media.source.controls=true;media.source.hidden=false;
    oldVideo.replaceWith(media.source);
    media.source.play().catch(()=>{});el('edgeFormat').value='mp4';el('edgeDuration').value=Math.min(60,Math.max(1,Math.ceil(media.source.duration)));
   }else{el('edgeImage').src=media.url;el('edgeFormat').value='png';}
   el('edgeSource').hidden=false;el('edgeEmpty').hidden=true;
   el('edgeSourceInfo').textContent=`${file.name} · ${width} × ${height} px`;
   status('Siap. Pilih sisi sumber lalu ekspor hasilnya.');
  }catch(error){if(candidate?.source)release(candidate);else if(candidate)URL.revokeObjectURL(candidate.url);status(error.message);}
  finally{loading=false;el('edgeFile').value='';update();}
 }
 function releaseVideoSource(video){video.pause();video.removeAttribute('src');video.load();}
 function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}
 async function seek(video,time){
  if(!video.seeking&&Math.abs(video.currentTime-time)<.000001&&video.readyState>=2)return;
  await waitFor(video,'seeked',()=>{video.currentTime=time;});
 }
 async function exportResult(){
  if(!media||busy||loading)return;
  const wasPlaying=media.kind==='video'&&!media.source.paused;
  if(media.kind==='video')media.source.pause();
  lock(true);cancelled=false;let output,exportVideo;
  try{
   const {width,height}=outputSize(),format=el('edgeFormat').value,level=el('edgeQuality').value;
   const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
   const ctx=canvas.getContext('2d',{alpha:format!=='mp4',willReadFrequently:true});
   const filename=`edge-${el('edgeSide').value}-${width}x${height}`;
   if(format!=='mp4'){
    draw(ctx,media.source,width,height);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,`image/${format}`,{economy:.65,standard:.82,high:.93,maximum:1}[level]));
    if(!blob)throw Error('Gambar tidak dapat diekspor pada ukuran ini.');
    if(!cancelled){download(blob,`${filename}.${format==='jpeg'?'jpg':'png'}`);status('Gambar selesai diekspor.');}
    else status('Ekspor dibatalkan.');
    return;
   }
   if(typeof VideoEncoder==='undefined'||typeof Mediabunny==='undefined')throw Error('Ekspor MP4 membutuhkan WebCodecs. Gunakan Chrome/Edge melalui localhost atau HTTPS.');
   const {Output,Mp4OutputFormat,BufferTarget,VideoSample,VideoSampleSource,Quality,canEncodeVideo}=Mediabunny;
   const bitrate=Math.round(Math.max(2000000,Math.min(160000000,width*height*60*{economy:.05,standard:.1,high:.2,maximum:.5}[level])));
   const quality=new Quality(level==='maximum'?{quantizer:18,bitrate,bitrateMode:'variable'}:{bitrate,bitrateMode:'variable'});
   const config={codec:'avc',quality,alpha:'discard',latencyMode:'quality',contentHint:'detail',hardwareAcceleration:'prefer-software'};
   const capability={width,height,quality,frameRate:60,alpha:'discard'};
   if(!await canEncodeVideo('avc',{...capability,hardwareAcceleration:config.hardwareAcceleration}))config.hardwareAcceleration='no-preference';
   if(!await canEncodeVideo('avc',{...capability,hardwareAcceleration:config.hardwareAcceleration}))throw Error('Encoder MP4 tidak mendukung ukuran ini. Coba canvas HD.');
   let source=media.source;
   if(media.kind==='video'){
    exportVideo=document.createElement('video');exportVideo.muted=true;exportVideo.playsInline=true;exportVideo.preload='auto';
    await waitFor(exportVideo,'loadeddata',()=>{exportVideo.src=media.url;exportVideo.load();});source=exportVideo;
   }
   const duration=Math.max(1,Math.min(60,Math.round(Number(el('edgeDuration').value)||10))),frames=duration*60;
   const target=new BufferTarget();output=new Output({format:new Mp4OutputFormat(),target});
   const track=new VideoSampleSource(config);output.addVideoTrack(track,{frameRate:60});await output.start();
   for(let i=0;i<frames;i++){
    if(cancelled)break;
    if(exportVideo)await seek(exportVideo,(i/60)%exportVideo.duration);
    draw(ctx,source,width,height);
    const pixels=ctx.getImageData(0,0,width,height);
    const sample=new VideoSample(pixels.data,{format:'RGBA',codedWidth:width,codedHeight:height,layout:[{offset:0,stride:width*4}],timestamp:i/60,duration:1/60});
    try{await track.add(sample,{keyFrame:i%120===0});}finally{sample.close();}
    if(i%12===0||i===frames-1){el('edgeExport').textContent=`Merender… ${Math.round((i+1)/frames*100)}%`;status(`Frame ${i+1} / ${frames} · 60 fps`);await new Promise(resolve=>setTimeout(resolve,0));}
   }
   if(cancelled){await output.cancel();status('Ekspor dibatalkan.');return;}
   status('Menyelesaikan MP4…');await output.finalize();
   if(cancelled){status('Ekspor dibatalkan.');return;}
   download(new Blob([target.buffer],{type:'video/mp4'}),`${filename}-${duration}s.mp4`);status(`MP4 selesai · ${width} × ${height} px · ${duration} detik · 60 fps.`);
  }catch(error){
   if(output&&output.state!=='finalized'&&output.state!=='canceled'){try{await output.cancel();}catch{}}
   status(`Ekspor gagal: ${error.message}`);
  }finally{
   if(exportVideo)releaseVideoSource(exportVideo);
   lock(false);if(wasPlaying)media.source.play().catch(()=>{});
  }
 }
 el('edgeFile').onchange=e=>load(e.target.files[0]);
 for(const type of ['dragenter','dragover'])el('edgeDrop').addEventListener(type,e=>{e.preventDefault();if(!busy)el('edgeDrop').classList.add('drag');});
 for(const type of ['dragleave','drop'])el('edgeDrop').addEventListener(type,e=>{e.preventDefault();el('edgeDrop').classList.remove('drag');});
 el('edgeDrop').addEventListener('drop',e=>load(e.dataTransfer.files[0]));
 for(const id of ['edgeSide','edgePreset','edgeWidth','edgeHeight','edgeFormat','edgeQuality'])el(id).onchange=update;
 el('edgeExport').onclick=exportResult;el('edgeCancel').onclick=()=>{cancelled=true;status('Membatalkan ekspor…');};
 function tick(){if(media?.kind==='video'&&!media.source.seeking&&media.source.readyState>=2)drawPreview();requestAnimationFrame(tick);}
 update();requestAnimationFrame(tick);
})();
