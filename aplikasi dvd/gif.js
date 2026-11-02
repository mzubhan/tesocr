'use strict';
// Decode only the requested patch; retain two compositing surfaces, not every frame.
async function loadGif(file){
 if(file.size>20*1024*1024)throw Error('GIF terlalu besar (maksimal 20 MB).');
 const buffer=await file.arrayBuffer(),bytes=new Uint8Array(buffer);
 if(bytes.length<13||String.fromCharCode(...bytes.slice(0,3))!=='GIF')throw Error('File GIF tidak valid.');
 const width=bytes[6]|bytes[7]<<8,height=bytes[8]|bytes[9]<<8;
 if(!width||!height||width*height>2048*2048)throw Error('Resolusi GIF terlalu besar. Gunakan maksimal 4 megapiksel.');
 const memoryBytes=width*height*16+buffer.byteLength*3;
 checkBudget(memoryBytes);
 const gif=Gifuct.parseGIF(buffer),frames=gif.frames.filter(frame=>frame.image);
 if(frames.some(frame=>{const d=frame.image.descriptor;return !d.width||!d.height||d.width*d.height>2048*2048;}))throw Error('Frame GIF terlalu besar.');
 if(!frames.length||frames.length>2000)throw Error('GIF kosong atau terlalu panjang (maksimal 2000 frame).');
 let duration=0;
 const ends=frames.map(frame=>{duration+=Math.max(20,(frame.gce?.delay||10)*10);return duration;});
 const raster=document.createElement('canvas');raster.width=width;raster.height=height;
 const patch=document.createElement('canvas');
 const media={kind:'gif',name:file.name,time:0,raster,patch,gif,frames,ends,gifDuration:duration/1000,memoryBytes,gifIndex:-1,previous:null,restore:null};
 try{renderGif(media);return media;}catch(error){disposeMedia(media);throw error;}
}
function renderGif(media){
 const milliseconds=(media.time*1000)%(media.gifDuration*1000);
 let target=media.ends.findIndex(end=>milliseconds<end);if(target<0)target=0;
 if(media.gifIndex===target)return;
 const c=media.raster.getContext('2d',{willReadFrequently:true});
 if(target<media.gifIndex){c.clearRect(0,0,media.raster.width,media.raster.height);media.gifIndex=-1;media.previous=null;media.restore=null;}
 while(media.gifIndex<target){
  const previous=media.previous;
  if(previous?.disposalType===2){const d=previous.dims;c.clearRect(d.left,d.top,d.width,d.height);}
  else if(previous?.disposalType===3&&media.restore)c.putImageData(media.restore,0,0);
  media.restore=null;
  const frame=Gifuct.decompressFrame(media.frames[++media.gifIndex],media.gif.gct,true),d=frame.dims;
  if(!d.width||!d.height||d.width*d.height>2048*2048)throw Error('Frame GIF terlalu besar.');
  if(frame.disposalType===3)media.restore=c.getImageData(0,0,media.raster.width,media.raster.height);
  media.patch.width=d.width;media.patch.height=d.height;
  media.patch.getContext('2d').putImageData(new ImageData(frame.patch,d.width,d.height),0,0);
  c.drawImage(media.patch,d.left,d.top);
  media.previous={dims:d,disposalType:frame.disposalType};
 }
}
