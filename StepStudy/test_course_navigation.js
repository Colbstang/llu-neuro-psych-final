const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function runtime(){
  const elements=new Map();
  for(const id of ['#side-panel','#side-content','#side-title','#close-side'])elements.set(id,{hidden:false,scrollTop:48,textContent:'Reference',innerHTML:'<div class="book-page-canvas">Synthetic page</div>',addEventListener(){}});
  const posted=[],options=[];
  const context={console,Map,JSON,Promise,Number,String,setTimeout,clearTimeout,
    $:id=>elements.get(id),state:{studyScope:'all',page:'chapter',referenceSearchFamilies:['first_aid'],ankiMediaPosition:{'context-one':2}},
    currentView:'read',ankiContext:{key:'context-one'},ankiReferenceMode:'books',activeRef:'term',referenceBook:'First Aid',rapidIndex:0,rapidRevealed:false,ankiCurrentCard:12,ankiCardRevealed:true,bookPanelActive:true,bookMarkMode:true,sideZoom:1.75,sourceViewerActive:null,
    stepCourseStarted:true,referenceOnly:false,stepCourseDefinitionSerial:0,sourceViewerSerial:0,semanticRequestSerial:0,semanticAbort:{abort(){}},DATA:{},
    referenceClone:value=>value?JSON.parse(JSON.stringify(value)):value,stepCourseActiveChapter:()=> 'chapter',stepCoursePost:(type,body)=>posted.push([type,body]),
    window:{scrollY:320,parent:{},scrollTo(value){this.scrollY=value.top;}},location:{origin:'http://localhost:8773'},document:{body:{classList:{add(){},remove(){}}}},
    addEventListener(){},queueMicrotask:fn=>fn(),requestAnimationFrame:fn=>{fn();return 1;},navigateView:value=>context.currentView=value,
    showSidePanel(title,html,opts={}){options.push(opts);elements.get('#side-title').textContent=title;elements.get('#side-content').innerHTML=html;elements.get('#side-panel').hidden=false;context.bookPanelActive=!!opts.bookPanel;if(!opts.keepZoom)context.sideZoom=1;},
    applySideZoom(){context.appliedZoom=context.sideZoom;},setBookMarkMode(value){context.bookMarkMode=value;context.appliedMarking=value;}
  };
  vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('./course_navigation.js'),'utf8'),context);
  return {context,elements,posted,options};
}
test('course Back restores book zoom, marking mode, panel controls and reading position',async()=>{
  const {context,elements,posted,options}=runtime();
  vm.runInContext('stepCourseNavRemember()',context);const id=posted[0][1].navigation.id;
  context.sideZoom=1;context.bookPanelActive=false;context.bookMarkMode=false;context.ankiCurrentCard=20;context.ankiCardRevealed=false;context.window.scrollY=0;elements.get('#side-content').scrollTop=0;
  await context.stepCourseNavRestore(id);
  assert.equal(context.sideZoom,1.75);assert.equal(context.bookPanelActive,true);assert.equal(context.bookMarkMode,true);
  assert.equal(context.appliedZoom,1.75);assert.equal(context.appliedMarking,true);
  assert.equal(context.ankiCurrentCard,12);assert.equal(context.ankiCardRevealed,true);
  assert.equal(elements.get('#side-content').scrollTop,48);assert.equal(context.window.scrollY,320);
  assert.equal(options.at(-1).keepZoom,true);assert.equal(options.at(-1).bookPanel,true);
  assert.equal(posted.length,1,'restoration must not push a new browser-history entry');
});
test('unknown course snapshot leaves the panel alone',async()=>{
  const {context,options}=runtime();assert.equal(await context.stepCourseNavRestore('missing'),false);assert.equal(options.length,0);
});
