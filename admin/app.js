(function(){
  "use strict";
  var state={challengeId:0,page:"dashboard",accountType:"users",decision:null,methods:[]};
  var paymentActionPaths={approve:"/approve",reject:"/reject",cancel:"/cancel"};
  var PRICE_LABELS={
    advertisement_district_hour:"Reklama · 1 tuman / 1 soat"
  };
  var REPORT_KIND_LABELS={
    profile:"Oddiy profil",
    business:"Biznes profil",
    product:"Mahsulot",
    service:"Xizmat",
    advertisement:"Reklama",
    listing:"E'lon",
    story:"Istoriya"
  };
  var $=function(id){return document.getElementById(id);};
  var esc=function(value){return String(value==null?"":value).replace(/[&<>"']/g,function(c){return({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c];});};
  var money=function(value){return new Intl.NumberFormat("uz-UZ").format(Number(value||0))+" so‘m";};
  var date=function(value){return value?new Date(Number(value)*1000).toLocaleString("uz-UZ"):"—";};
  function api(path,options){
    var init=Object.assign({credentials:"same-origin",headers:{"Content-Type":"application/json"}},options||{});
    return fetch(path,init).then(function(response){
      if(response.ok&&(response.headers.get("content-type")||"").indexOf("application/json")<0)return response;
      return response.json().catch(function(){return {};}).then(function(data){
        if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:(data.detail&&data.detail.detail)||("Xatolik "+response.status));
        return data;
      });
    });
  }
  function toast(text,error){
    var node=document.createElement("div");node.className="toast"+(error?" error":"");node.textContent=text;
    $("toastRegion").appendChild(node);setTimeout(function(){node.remove();},3500);
  }
  function loginStatus(text,error){$("message").textContent=text||"";$("message").classList.toggle("error",!!error);}
  function setBusy(button,busy){if(button)button.disabled=!!busy;}
  function badge(status){return '<span class="badge '+esc(status)+'">'+esc(status||"—")+"</span>";}
  function empty(text){return '<div class="list-state">'+esc(text)+"</div>";}
  function showApp(admin){
    $("loginCard").hidden=true;$("adminApp").hidden=false;
    $("currentAdminId").textContent="Admin #"+admin.tg_id;
    navigate("dashboard");
  }
  function closeDrawer(){
    $("adminSidebar").classList.remove("open");$("drawerShade").classList.remove("open");
    $("adminNavToggle").setAttribute("aria-expanded","false");
  }
  function navigate(page){
    state.page=page;closeDrawer();
    document.querySelectorAll(".nav-item").forEach(function(n){n.classList.toggle("active",n.dataset.adminPage===page);});
    document.querySelectorAll(".page").forEach(function(n){n.classList.toggle("active",n.dataset.pagePanel===page);});
    ({dashboard:loadDashboard,payments:loadPayments,pricing:loadPricing,accounts:loadAccounts,content:loadContent,reports:loadReports,audit:loadAudit}[page]||function(){})();
  }
  function loadDashboard(){
    $("dashboardMetrics").innerHTML=empty("Yuklanmoqda…");
    api("/api/admin/dashboard").then(function(data){
      var cards=[
        ["KUTILAYOTGAN TO‘LOVLAR",data.payments.pending.count,money(data.payments.pending.amount),"primary"],
        ["FOYDALANUVCHILAR",data.users.total,"24 soat: "+data.users.new_24h,""],
        ["BIZNESLAR",data.businesses.total,"30 kun: "+data.businesses.new_30d,""],
        ["OCHIQ SHIKOYATLAR",data.reports.open,"Ko‘rib chiqish navbati",""]
      ];
      $("dashboardMetrics").innerHTML=cards.map(function(c){return '<article class="metric '+c[3]+'"><small>'+c[0]+'</small><strong>'+esc(c[1])+'</strong><span>'+esc(c[2])+"</span></article>";}).join("");
      $("dashboardActivity").innerHTML=data.activity.length?data.activity.map(function(row){return '<div class="activity-row"><strong>'+esc(row.action)+'</strong><span>'+esc(row.target_kind)+" #"+esc(row.target_id)+'</span><small class="subtle">'+date(row.created_at)+"</small></div>";}).join(""):empty("Hali admin harakati yo‘q.");
    }).catch(function(e){$("dashboardMetrics").innerHTML=empty(e.message);toast(e.message,true);});
  }
  function loadPayments(){
    $("paymentsBody").innerHTML='<tr><td colspan="6">Yuklanmoqda…</td></tr>';
    var query=new URLSearchParams({status:$("paymentStatus").value,service_type:$("paymentService").value});
    api("/api/admin/payments?"+query).then(function(rows){
      $("paymentsBody").innerHTML=rows.length?rows.map(function(row){return "<tr><td><strong>"+esc(row.request_code)+"</strong></td><td>"+esc(row.service_type)+"</td><td>"+money(row.amount)+"</td><td>"+badge(row.status)+"</td><td>"+date(row.created_at)+'</td><td><button class="secondary compact" data-payment="'+row.id+'">Ko‘rish</button></td></tr>';}).join(""):'<tr><td colspan="6">To‘lov topilmadi.</td></tr>';
    }).catch(function(e){$("paymentsBody").innerHTML='<tr><td colspan="6">'+esc(e.message)+"</td></tr>";});
  }
  function openPayment(id){
    Promise.all([api("/api/admin/payments/"+id),api("/api/admin/payments/"+id+"/receipt")]).then(function(values){
      var payment=values[0],response=values[1];$("receiptTitle").textContent=payment.request_code+" · "+money(payment.amount);
      if(response instanceof Response){response.blob().then(function(blob){$("receiptImage").src=URL.createObjectURL(blob);});}
      var buttons='<button class="secondary" data-close="paymentReceiptDialog">Yopish</button>';
      if(payment.status==="pending")buttons+='<button class="danger" data-decision="reject" data-id="'+id+'">Rad etish</button><button data-decision="approve" data-id="'+id+'">Tasdiqlash</button>';
      if(payment.status==="approved")buttons+='<button class="warn" data-decision="cancel" data-id="'+id+'">Bekor qilish</button>';
      $("receiptActions").innerHTML=buttons;$("paymentReceiptDialog").showModal();
    }).catch(function(e){toast(e.message,true);});
  }
  function requestDecision(kind,id,meta){
    state.decision={kind:kind,id:id,meta:meta||{}};
    $("decisionTitle").textContent={approve:"To‘lovni tasdiqlash",reject:"To‘lovni rad etish",cancel:"To‘lovni bekor qilish",hide:"Kontentni yashirish",restore:"Kontentni tiklash",remove:"Kontentni olib tashlash",restrict:"Profilni cheklash",unrestrict:"Cheklovni olish",resolve:"Shikoyatni hal qilish",dismiss:"Shikoyatni rad etish"}[kind]||"Qarorni tasdiqlang";
    $("decisionText").textContent="Ushbu amal audit tarixiga yoziladi.";
    $("decisionReason").value="";$("paymentDecisionDialog").showModal();
  }
  function submitDecision(){
    var d=state.decision,reason=$("decisionReason").value.trim();
    if(!d)return Promise.resolve();
    if(["reject","cancel","hide","restore","remove","restrict","unrestrict","resolve","dismiss"].indexOf(d.kind)>=0&&!reason){toast("Sabab kiritilishi shart.",true);return Promise.reject(new Error("reason"));}
    var path,body={reason:reason};
    if(paymentActionPaths[d.kind])path="/api/admin/payments/"+d.id+paymentActionPaths[d.kind];
    if(["hide","restore","remove"].indexOf(d.kind)>=0)path="/api/admin/content/"+d.meta.kind+"/"+d.id+"/"+d.kind;
    if(["restrict","unrestrict"].indexOf(d.kind)>=0){path="/api/admin/accounts/"+d.meta.actorType+"/"+d.id+"/"+d.kind;body.restriction=d.meta.restriction;}
    if(["resolve","dismiss"].indexOf(d.kind)>=0){path="/api/admin/reports/"+d.id+"/"+d.kind;body={resolution:reason,moderation_action:d.meta.action||"none"};}
    return api(path,{method:"POST",body:JSON.stringify(body)}).then(function(){
      $("paymentDecisionDialog").close();$("paymentReceiptDialog").close();toast("Amal bajarildi.");
      ({payments:loadPayments,accounts:loadAccounts,content:loadContent,reports:loadReports}[state.page]||loadDashboard)();
    });
  }
  function loadPricing(){
    Promise.all([api("/api/admin/prices"),api("/api/admin/payment-methods")]).then(function(values){
      var prices=values[0];state.methods=values[1];
      $("pricesList").innerHTML=prices.map(function(row){return '<div class="setting-row"><div><strong>'+esc(PRICE_LABELS[row.price_code]||row.price_code)+'</strong><div class="subtle">'+esc(row.service_type)+'</div></div><input type="number" min="0" value="'+row.amount_uzs+'" data-price-value="'+row.id+'"><button class="secondary compact" data-save-price="'+row.id+'">Saqlash</button></div>';}).join("")||empty("Narx yo‘q.");
      $("paymentMethodsList").innerHTML=state.methods.map(function(row){return '<div class="setting-row"><div><strong>'+esc(row.name)+'</strong><div class="subtle">'+esc(row.recipient_name)+'</div></div>'+badge(row.active?"active":"inactive")+'<button class="secondary compact" data-edit-method="'+row.id+'">Tahrirlash</button></div>';}).join("")||empty("To‘lov usuli yo‘q.");
    }).catch(function(e){toast(e.message,true);});
  }
  function savePrice(id,button){
    var input=document.querySelector('[data-price-value="'+id+'"]');setBusy(button,true);
    api("/api/admin/prices/"+id,{method:"PUT",body:JSON.stringify({amount:Number(input.value),active:true,reason:"Admin paneldan narx yangilandi"})}).then(function(){toast("Narx saqlandi.");}).catch(function(e){toast(e.message,true);}).finally(function(){setBusy(button,false);});
  }
  function openMethod(id){
    var row=state.methods.find(function(x){return Number(x.id)===Number(id);})||{};
    $("methodId").value=row.id||"";$("methodName").value=row.name||"";$("methodType").value=row.method_type||"manual_card";$("methodRecipient").value=row.recipient_name||"";
    $("methodDetails").value=row.details_json||"{}";$("methodInstructions").value=row.instructions||"";$("methodActive").checked=row.active!==0;$("paymentMethodDialog").showModal();
  }
  function saveMethod(){
    var id=$("methodId").value,details;try{details=JSON.parse($("methodDetails").value||"{}");}catch(e){toast("Rekvizit JSON noto‘g‘ri.",true);return Promise.reject(e);}
    var body={name:$("methodName").value.trim(),method_type:$("methodType").value.trim(),recipient_name:$("methodRecipient").value.trim(),details:details,instructions:$("methodInstructions").value.trim(),active:$("methodActive").checked};
    return api("/api/admin/payment-methods"+(id?"/"+id:""),{method:id?"PUT":"POST",body:JSON.stringify(body)}).then(function(){$("paymentMethodDialog").close();toast("To‘lov usuli saqlandi.");loadPricing();});
  }
  function loadAccounts(){
    state.accountType=$("accountType").value;var query=new URLSearchParams({q:$("accountSearch").value.trim(),status:$("accountStatus").value,page:"1"});
    $("accountsList").innerHTML=empty("Yuklanmoqda…");
    api("/api/admin/"+state.accountType+"?"+query).then(function(data){
      $("accountsList").innerHTML=data.items.length?data.items.map(function(row){return '<button class="record-row secondary" data-account="'+row.id+'"><span class="record-main"><strong>'+esc(row.name)+'</strong><small>#'+row.id+" · "+esc(row.login||row.yon||"")+'</small></span>'+badge(row.status||row.role||"user")+'<span>'+esc((row.active_restrictions||[]).join(", ")||"Cheklov yo‘q")+"</span><span>→</span></button>";}).join(""):empty("Profil topilmadi.");
    }).catch(function(e){$("accountsList").innerHTML=empty(e.message);});
  }
  function openAccount(id){
    var type=state.accountType==="users"?"user":"business";
    api("/api/admin/"+state.accountType+"/"+id).then(function(row){
      var restrictions=row.active_restrictions||[];
      $("accountDetail").innerHTML='<div class="panel-head"><div><div class="eyebrow">'+esc(type.toUpperCase())+'</div><h2>'+esc(row.name)+'</h2></div>'+badge(row.status||row.role||"active")+'</div><div class="detail-body"><div class="detail-grid"><div class="detail-tile"><small>ID</small><strong> #'+row.id+'</strong></div><div class="detail-tile"><small>Telefon</small><strong> '+esc(row.phone||"—")+'</strong></div><div class="detail-tile"><small>Yaratilgan</small><strong> '+date(row.created_at)+'</strong></div><div class="detail-tile"><small>Cheklov</small><strong> '+esc(restrictions.join(", ")||"Yo‘q")+'</strong></div></div><div class="action-row"><button class="'+(restrictions.includes("content_hidden")?"warn":"secondary")+'" data-account-action="'+(restrictions.includes("content_hidden")?"unrestrict":"restrict")+'" data-restriction="content_hidden" data-id="'+id+'" data-type="'+type+'">Public ko‘rinish</button><button class="'+(restrictions.includes("account_blocked")?"danger":"secondary")+'" data-account-action="'+(restrictions.includes("account_blocked")?"unrestrict":"restrict")+'" data-restriction="account_blocked" data-id="'+id+'" data-type="'+type+'">Hisob bloki</button></div></div>';
    }).catch(function(e){$("accountDetail").innerHTML=empty(e.message);});
  }
  function loadContent(){
    var query=new URLSearchParams({kind:$("contentKind").value,status:$("contentStatus").value,q:$("contentSearch").value.trim(),page:"1"});
    $("contentList").innerHTML=empty("Yuklanmoqda…");
    api("/api/admin/content?"+query).then(function(data){
      $("contentList").innerHTML=data.items.length?data.items.map(function(row){var actions=row.moderation_status==="visible"?'<button class="warn compact" data-content-action="hide" data-id="'+row.id+'" data-kind="'+row.kind+'">Yashirish</button>':'<button class="secondary compact" data-content-action="restore" data-id="'+row.id+'" data-kind="'+row.kind+'">Tiklash</button>';actions+='<button class="danger compact" data-content-action="remove" data-id="'+row.id+'" data-kind="'+row.kind+'">Olib tashlash</button>';return '<div class="record-row"><span class="record-main"><strong>'+esc(row.title||"Nomsiz")+'</strong><small>'+esc(row.owner_name||"")+'</small></span>'+badge(row.kind)+' '+badge(row.moderation_status)+'<span class="action-row">'+actions+"</span></div>";}).join(""):empty("Kontent topilmadi.");
    }).catch(function(e){$("contentList").innerHTML=empty(e.message);});
  }
  function loadReports(){
    $("reportsList").innerHTML=empty("Yuklanmoqda…");
    api("/api/admin/reports?status="+encodeURIComponent($("reportStatus").value)).then(function(data){
      $("reportsList").innerHTML=data.items.length?data.items.map(function(row){var actions="";if(["open","reviewing"].includes(row.status))actions='<button class="secondary compact" data-report-action="assign" data-id="'+row.id+'">Qabul qilish</button><button class="warn compact" data-report-action="resolve" data-id="'+row.id+'">Hal qilish</button><button class="danger compact" data-report-action="dismiss" data-id="'+row.id+'">Rad etish</button>';return '<div class="record-row"><span class="record-main"><strong>'+esc(row.reason_code)+'</strong><small>'+esc(REPORT_KIND_LABELS[row.content_kind]||row.content_kind)+" #"+row.content_id+" · "+esc(row.comment)+'</small></span>'+badge(row.status)+'<span>'+date(row.created_at)+'</span><span class="action-row">'+actions+"</span></div>";}).join(""):empty("Shikoyat topilmadi.");
    }).catch(function(e){$("reportsList").innerHTML=empty(e.message);});
  }
  function loadAudit(){
    var query=new URLSearchParams({action:$("auditAction").value.trim(),admin_tg_id:$("auditAdminId").value.trim(),page:"1"});
    $("auditList").innerHTML=empty("Yuklanmoqda…");$("auditExport").href="/api/admin/audit/export.csv?"+query;
    api("/api/admin/audit?"+query).then(function(data){
      $("auditList").innerHTML=data.items.length?data.items.map(function(row){return '<div class="activity-row"><strong>'+esc(row.action)+'</strong><span>'+esc(row.target_kind)+" #"+esc(row.target_id)+" · "+esc(row.reason||"")+'</span><small class="subtle">'+date(row.created_at)+"</small></div>";}).join(""):empty("Audit hodisasi yo‘q.");
    }).catch(function(e){$("auditList").innerHTML=empty(e.message);});
  }
  document.addEventListener("click",function(event){
    var target=event.target.closest("button,a");if(!target)return;
    if(target.dataset.adminPage)navigate(target.dataset.adminPage);
    if(target.dataset.refresh)navigate(target.dataset.refresh);
    if(target.dataset.close){var dialog=$(target.dataset.close);if(dialog)dialog.close();}
    if(target.dataset.payment)openPayment(target.dataset.payment);
    if(target.dataset.decision)requestDecision(target.dataset.decision,target.dataset.id);
    if(target.dataset.savePrice)savePrice(target.dataset.savePrice,target);
    if(target.dataset.editMethod)openMethod(target.dataset.editMethod);
    if(target.dataset.account)openAccount(target.dataset.account);
    if(target.dataset.accountAction)requestDecision(target.dataset.accountAction,target.dataset.id,{actorType:target.dataset.type,restriction:target.dataset.restriction});
    if(target.dataset.contentAction)requestDecision(target.dataset.contentAction,target.dataset.id,{kind:target.dataset.kind});
    if(target.dataset.reportAction){if(target.dataset.reportAction==="assign")api("/api/admin/reports/"+target.dataset.id+"/assign",{method:"POST",body:"{}"}).then(loadReports).catch(function(e){toast(e.message,true);});else requestDecision(target.dataset.reportAction,target.dataset.id,{action:"none"});}
  });
  $("adminNavToggle").addEventListener("click",function(){var open=!$("adminSidebar").classList.contains("open");$("adminSidebar").classList.toggle("open",open);$("drawerShade").classList.toggle("open",open);this.setAttribute("aria-expanded",String(open));});
  $("drawerShade").addEventListener("click",closeDrawer);
  $("loadPayments").addEventListener("click",loadPayments);$("loadAccounts").addEventListener("click",loadAccounts);$("loadContent").addEventListener("click",loadContent);$("loadReports").addEventListener("click",loadReports);$("loadAudit").addEventListener("click",loadAudit);
  $("newPaymentMethod").addEventListener("click",function(){openMethod(0);});
  $("decisionForm").addEventListener("submit",function(event){event.preventDefault();setBusy($("decisionSubmit"),true);submitDecision().catch(function(e){if(e.message!=="reason")toast(e.message,true);}).finally(function(){setBusy($("decisionSubmit"),false);});});
  $("paymentMethodForm").addEventListener("submit",function(event){event.preventDefault();saveMethod().catch(function(e){toast(e.message,true);});});
  $("idForm").addEventListener("submit",function(event){event.preventDefault();var button=event.submitter;setBusy(button,true);loginStatus("Kod yuborilmoqda…");api("/api/admin/auth/start",{method:"POST",body:JSON.stringify({tg_id:$("tgId").value.trim()})}).then(function(data){state.challengeId=data.challenge_id;$("idForm").hidden=true;$("codeForm").hidden=false;$("code").focus();loginStatus("Kod Telegramga yuborildi.");}).catch(function(e){loginStatus(e.message,true);}).finally(function(){setBusy(button,false);});});
  $("codeForm").addEventListener("submit",function(event){event.preventDefault();var button=event.submitter;setBusy(button,true);api("/api/admin/auth/verify",{method:"POST",body:JSON.stringify({challenge_id:state.challengeId,code:$("code").value.trim()})}).then(showApp).catch(function(e){loginStatus(e.message,true);}).finally(function(){setBusy(button,false);});});
  $("logout").addEventListener("click",function(){api("/api/admin/auth/logout",{method:"POST",body:"{}"}).finally(function(){location.reload();});});
  api("/api/admin/auth/me").then(showApp).catch(function(){});
})();
