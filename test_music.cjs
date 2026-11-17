const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app.js','utf8').split('// 默认尝试播放；')[1];
class Events {
  constructor(){this.events=new Map();this.attrs={};}
  addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,new Set());this.events.get(type).add(fn);}
  removeEventListener(type,fn){this.events.get(type)?.delete(fn);}
  async emit(type,event={}){for(const fn of [...(this.events.get(type)||[])])await fn({type,...event});}
  setAttribute(key,value){this.attrs[key]=value;}
}
async function setup(blocked){
  const audio=new Events(),button=new Events(),document=new Events();
  audio.paused=true;audio.calls=0;
  audio.play=async()=>{audio.calls++;if(blocked&&audio.calls===1)throw new Error('NotAllowedError');audio.paused=false;await audio.emit('play');};
  audio.pause=()=>{audio.paused=true;audio.emit('pause');};
  vm.runInNewContext('// 默认尝试播放；'+source,{$:id=>id==='winter-bgm'?audio:button,document,hint:()=>{}});
  await new Promise(resolve=>setImmediate(resolve));
  return {audio,button,document};
}
test('打开页面即尝试播放，并按真实状态更新音符',async()=>{
  const {audio,button}=await setup(false);assert.equal(audio.calls,1);assert.equal(audio.paused,false);assert.equal(button.attrs['aria-pressed'],'true');
});
test('自动播放受限后，首次页面交互再播放',async()=>{
  const {audio,document}=await setup(true);assert.equal(audio.paused,true);
  await document.emit('pointerdown',{target:{closest:()=>null}});assert.equal(audio.paused,false);assert.equal(audio.calls,2);
});
test('手动暂停后，页面交互不重新开启音乐',async()=>{
  const {audio,button,document}=await setup(false);await button.emit('click');assert.equal(audio.paused,true);
  await document.emit('pointerdown');assert.equal(audio.calls,1);assert.equal(audio.paused,true);
});
test('点击音符本身不会同时触发默认播放逻辑',async()=>{
  const {audio,button,document}=await setup(true);
  await document.emit('pointerdown',{target:{closest:()=>button}});assert.equal(audio.calls,1);
  await button.emit('click');assert.equal(audio.calls,2);assert.equal(audio.paused,false);
});
