:!0,isEditable:!1,selectedText:""
}
),ie=e=>w("GetSelectedTextViaCopy",e=>e.SelectedTextViaCopy?.payload,{
afterText:"",beforeText:"",contents:"",selectedText:""
}
,{
payload:{
copyMode:e
}

}
),se=()=>w("GetGranolaInstallation",e=>e.GranolaInstallation?.payload.state,"unknown",{
timeoutOverride:1e3
}
),oe=()=>w("GetRunningApps",e=>e.RunningApps?.payload.apps,[]),ae=(e,t)=>w("ActivateRunningApp",e=>e.ACK,!1,{
payload:{
preferredProcessId:e,executablePath:t
}

}
),ce=(e,t,n,i=d.B4.Split)=>w("PositionBrowserWindow",e=>{
const t=e.BrowserWindowPositioned?.payload;
if(!t)return;
const n=t.browserAnchored?"anchored":!1===t.retryable?"terminal_failure":"retryable_failure";
if((i===d.B4.Split||t.browserAnchored&&!t.alreadyPlaced)&&m.qd.count("helper.browser_window_position",1,{
attributes:{
outcome:n
}

}
),!t.browserAnchored||null==t.recorderX||null==t.recorderY||null==t.recorderWidth||null==t.recorderHeight)return{
browserAnchored:!1,retryable:t.retryable
}
;
const s={
x:t.recorderX,y:t.recorderY,width:t.recorderWidth,height:t.recorderHeight
}
;
return{
browserAnchored:!0,retryable:!1,recorder:p.H8?r.screen.screenToDipRect(null,s):s,...t.alreadyPlaced?{
alreadyPlaced:!0
}
:{

}

}

}
,{
browserAnchored:!1,retryable:!0
}
,{
payload:{
browserName:e,recorderWidthFraction:t,recorderMinWidth:n,mode:i
}

}
),le=e=>w("IsReady",e=>{
if(e.ACK)return{
ready:!0,helperMainEntryMs:e.HelperLaunchTiming?.helperMainEntryMs,tapEnabled:e.TapState?.payload.tapEnabled
}

}
,{
ready:!1
}
,{
timeoutOverride:e||1e4
}
),ue=()=>w("StartAllIntervals",e=>e.ACK,!1,{
timeoutOverride:1e4
}
),de=()=>w("StopAllIntervals",e=>e.ACK,!1,{
timeoutOverride:1e4,propagateError:!0
}
),pe=["com.google.Chrome","com.google.Chrome.beta","com.google.Chrome.dev","com.google.Chrome.canary"],he=(e,t,n={

}
,i)=>{
const a=(0,u.Br)(n);
v.RA.lastPasteTextSentAt=Date.now();
const c=(()=>{
const e=ve((0,y.getFeatureFlagData)(f.TO.ChromePasteDropWorkaround));
return!!e.enabled&&(e.payload?.bundleIds??pe).includes(b.ZZ.appBundleID)
}
)()?r.clipboard.readBuffer("com.wispr.flow.restore-text"):void 0,l=c?.length?c.toString("utf8"):r.clipboard.readText();
v.RA.mostRecentClipboardContentsString=l,v.RA.lastPasteUUID=a,s().info(`Saved clipboard contents before paste, length: ${
l.length
}
`);
const d={
pasteId:a,text:e
}
;
return null!=t&&(d.htmlText=t),i&&(d.logAttributes=i),(0,u.Db)(a,e),(0,g.W)()&&(d.captureKeystrokeTrace=!0),w("PasteText",()=>{

}
,void 0,{
payload:d,propagateError:!0
}
).catch(e=>{
const t=String(e);
(0,u.gK)(a,"helper_request_failed"),t===o.Hs||t===o.X8?s().warn("Paste text to app failed — helper unavailable",{
customAttributes:{
pasteId:a,reason:t
}

}
):s().severe("Paste text to app failed",{
customAttributes:{
pasteId:a,error:t
}

}
)
}
),a
}
,me=(e,t)=>{
w(e,()=>{

}
,void 0,{
payload:t,propagateError:!0
}
).catch(t=>{
s().sensitive("Fire-and-forget helper request failed",{
customAttributes:{
actionType:e,error:String(t)
}

}
)
}
)
}
,fe=["pollIntervalSeconds","stormWindowSeconds","stormThreshold","wakeExemptSeconds"],ge=["pasteDelayMs"],be=(e,t)=>{
if("object"!=typeof e||null===e||"boolean"!=typeof e.enabled)return null;
const n=e.enabled,r=e.payload;
if("object"!=typeof r||null===r)return{
enabled:n
}
;
const i={

}
;
for(const e of t){
const t=r[e];
"number"==typeof t&&Number.isFinite(t)&&(i[e]=t)
}
return{
enabled:n,numbers:i
}

}
,Ae=e=>{
const t=be(e,fe);
return t?t.numbers?{
enabled:t.enabled,payload:t.numbers
}
:{
enabled:t.enabled
}
:{
enabled:!1
}

}
,ye=e=>{
const t=be(e,ge);
return t?t.numbers?{
enabled:t.enabled,payload:t.numbers
}
:{
enabled:t.enabled
}
:{
enabled:!1
}

}
,ve=e=>{
const t=e??{

}
,n=!0===t.enabled,r=t.payload?.bundleIds,i=Array.isArray(r)&&r.length>0&&r.every(e=>"string"==typeof e)?r:null;
return i?{
enabled:n,payload:{
bundleIds:i
}

}
:{
enabled:n
}

}
,we=new Set,Me=()=>we.clear(),_e=e=>{
const t={

}
;
for(const n in e)c.has(n)&&(t[n]=e[n]);
t["tap-watchdog"]=Ae(t["tap-watchdog"]),t["remote-viewer-paste"]=ye(t["remote-viewer-paste"]),t["windows-shortcut-masking-v2"]??={
enabled:!1
}
,t["fn-hardware-fallback"]??={
enabled:!1
}
,t["fn-conflict-notice"]??={
enabled:!1
}
,t["fn-listener-telemetry"]??={
enabled:!0
}
,t["shortcut-conflict-detection"]??={
enabled:!1
}
,t["notetaker-airpods-audio-diagnostics"]??={
enabled:!1
}
;
const n=ve(t["chrome-paste-drop-workaround"]);
t["chrome-paste-drop-workaround"]=n;
const r=l(a,t,"");
if(r.length>0){
const e=(0,y.areFeatureFlagsResolved)();
m.qd.count("helper.feature_flags_not_sent",1,{
attributes:{
reason:e?"contract_violation":"flags_unresolved"
}

}
);
const t=[...r].sort().join(",");
return void(e?we.has(t)||(we.add(t),s().error("Not sending helper feature flags — payload would fail the helper schema",{
customAttributes:{
violations:t,flagsResolved:e
}

}
)):s().warn("Not sending helper feature flags — payload would fail the helper schema",{
customAttributes:{
violations:t,flagsResolved:e
}

}
))
}
me("UpdateFeatureFlags",{
featureFlags:t
}
)
}
,Se=e=>w("BleStartPairing",e=>e.ACK,!1,{
payload:{
scanDurationSeconds:e
}
,timeoutOverride:35e3
}
),Ee=e=>w("BleSetPairedDevice",e=>e.ACK,!1,{
payload:{
deviceId:e
}

}
),Oe=e=>{
me("ShowFloatingWebPanel",e)
}
,Ce=e=>{
me("HideFloatingWebPanel",{
panelId:e
}
)
}
,Ie=(e,t,n,r,i,s)=>{
me("PostConsentChatMessage",{
text:e,sessionId:t,platform:n,meetingUrl:r??void 0,chatButtonLabels:i,meetLegacyWalk:s.legacyWalk,meetOverflowMenu:s.overflowMenu,meetOverflowButtonLabels:s.overflowButtonLabels
}
)
}
,ke=e=>w("CancelConsentChatMessage",()=>{

}
,void 0,{
payload:{
sessionId:e
}
,propagateError:!0
}
),ze=()=>{
const e=v.RA.helper.process?.stdin;
return!!e&&!e.destroyed
}
,Te=()=>{
if(!ze())return void s().info("Skipping helper shutdown request — helper process already gone");
const e={
uuid:crypto.randomUUID(),HelperAppShutdown:!0
}
,t={
resolve:()=>s().info("Helper shutdown request acknowledged"),reject:e=>s().warn(`Helper shutdown request failed: ${
e
}
`)
}
;
o.oJ.sendRequest(e,t)
}

}
,48399(e,t,n){
"use strict";
n.d(t,{
EN:()=>u,EU:()=>a,FH:()=>i,fn:()=>l,iT:()=>c,mi:()=>o
}
);
var r=n(59625);
const i="helper_error:audio_hal_watchdog",s=new Set(["addDeviceListener","addSystemListener","audioDeviceIDs","defaultInputDevice","deviceAlive","deviceAliveRead","deviceName","deviceNameRead","deviceTransportType","deviceUID","deviceUIDR