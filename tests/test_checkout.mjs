import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source=readFileSync(new URL('../payment_service/web/checkout.js',import.meta.url),'utf8');
async function checkout(testMode,status='pending') {
  const nodes=new Map();let options;let opened=0;
  function node(selector) {
    if(!nodes.has(selector))nodes.set(selector,{handlers:{},addEventListener(name,fn){this.handlers[name]=fn;}});
    return nodes.get(selector);
  }
  class Razorpay {
    constructor(value){options=value;}
    on(){}
    open(){opened++;}
  }
  const context={URLSearchParams,Intl,location:{hash:'#claim='+'a'.repeat(64)},
    document:{querySelector:node},window:{Razorpay},
    fetch:async(path,request)=>{
      assert.equal(path,'/api/purchase');
      assert.equal(JSON.parse(request.body).claim,'a'.repeat(64));
      return {ok:true,json:async()=>({amount:9900,currency:'USD',documents:2,order_id:'order_fixture',key_id:'rzp_test_fixture',test_mode:testMode,status})};
    }};
  await vm.runInNewContext(source,context);
  node('#pay').handlers.click();
  return {options,opened,nodes};
}
test('test checkout offers a test card and keeps the server order/price',async()=>{
  const result=await checkout(true);
  assert.equal(result.opened,1);
  assert.equal(result.options.order_id,'order_fixture');
  assert.equal(result.options.amount,9900);
  assert.equal(result.options.currency,'USD');
  assert.equal(result.options.config.display.blocks.test_cards.instruments[0].method,'card');
  assert.equal(result.options.config.display.preferences.show_default_blocks,false);
});
test('live checkout retains normal merchant payment methods',async()=>{
  const result=await checkout(false);
  assert.equal(result.options.config,undefined);
  assert.equal(result.nodes.get('#test').hidden,true);
});
test('paid purchases cannot start another checkout',async()=>{
  const result=await checkout(true,'paid');
  assert.equal(result.opened,0);
  assert.equal(result.nodes.get('#pay').disabled,true);
});
