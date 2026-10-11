/* Browser history owns navigation. Snapshots hold location, never ratings. */
(function(scope){
  'use strict';
  function signature(state){return JSON.stringify([state.view,state.topicId,state.course?.key||'',state.definition||'']);}
  function create(history,location,onRestore,onChange=()=>{}){
    let current=null,restoring=false;
    function write(state,replace=false){
      current=state;
      if(restoring)return;
      const previous=history.state?.stepNavigation;
      const method=replace||!previous||signature(previous)===signature(state)?'replaceState':'pushState';
      history[method]({stepNavigation:state},'', '#'+state.view);
      onChange();
    }
    async function restore(event){
      const state=event.state?.stepNavigation;
      if(!state)return false;
      restoring=true;current=state;
      try{await onRestore(state);}finally{restoring=false;onChange();}
      return true;
    }
    return {write,restore,get current(){return current;},get restoring(){return restoring;}};
  }
  const api={create,signature};if(typeof module!=='undefined'&&module.exports)module.exports=api;
  scope.StepNavigation=api;
})(typeof window!=='undefined'?window:this);
