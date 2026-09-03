(function(){
  function init(){
    var els=document.querySelectorAll('.anim');
    if(!els.length)return;
    var ios=new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){e.target.classList.add('visible');ios.unobserve(e.target);}
      });
    },{threshold:.12});
    els.forEach(function(el){ios.observe(el);});
    var io2=new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(!e.isIntersecting)return;
        var el=e.target,t=parseFloat(el.getAttribute('data-target'));
        if(isNaN(t))return;
        var dur=900,start=performance.now();
        var dec=t%1!==0;
        function tick(now){
          var p=Math.min((now-start)/dur,1);
          p=1-Math.pow(1-p,3);
          var v=p*t;
          el.textContent=dec?v.toFixed(1):Math.round(v);
          if(p<1)requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
        io2.unobserve(el);
      });
    },{threshold:.5});
    document.querySelectorAll('[data-target]').forEach(function(el){io2.observe(el);});
  }
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',init):init();
})();
