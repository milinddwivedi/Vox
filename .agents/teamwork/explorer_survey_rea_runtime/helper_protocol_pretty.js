opTickTime=Date.now();
constructor(){

}
static startKeepaliveTimer(){
(0,T.iM)(this.keepaliveInterval),this.lastResponseTime=Date.now(),this.lastEventLoopTickTime=Date.now(),this.keepaliveErrorCount=0,i().info("Starting keepalive timer"),this.keepaliveInterval=setInterval(async()=>{
const e=Date.now()-this.lastEventLoopTickTime;
if(this.lastEventLoopTickTime=Date.now(),!A.RA.helper.ready||!A.RA.helper.process||"active"!==A.RA.systemState)return void i().warn(`Skipping keepalive. Should never happen. Helper ready: ${
A.RA.helper.ready
}
, Helper process null: ${
null===A.RA.helper.process
}
, System state: ${
A.RA.systemState
}
`);
const t=Date.now()-this.lastResponseTime;
return e>this.KEEPALIVE_RELAUNCH_THRESHOLD_MS&&e>=.8*t?(i().info("Keepalive: event loop was frozen, skipping relaunch",{
customAttributes:{
tickGap:e,timeSinceLastResponse:Math.round(t/1e3)
}

}
),this.lastResponseTime=Date.now(),this.lastEventLoopTickTime=Date.now(),void(0,He.sendHelperRequest)("IsReady")):!(K.H8&&Date.now()<this.wakeRecoveryDeadline)&&t>this.KEEPALIVE_RELAUNCH_THRESHOLD_MS?(this.lastResponseTime=Date.now(),this.keepaliveErrorCount++,A.RA.helper.ready=!1,A.RA.helper.pendingDeathClass="keepalive_timeout",this.keepaliveErrorCount<=3&&i().warn("No helper response received, killing helper service and relaunching",{
customAttributes:{
timeSinceLastResponse:String(Math.round(t/1e3)),eventLoopTickGap:String(e),keepaliveErrorCount:String(this.keepaliveErrorCount)
}

}
),void await(0,je.H3)("keepaliveTimer")):void(t>this.KEEPALIVE_PING_THRESHOLD_MS&&(0,He.sendHelperRequest)("IsReady"))
}
,this.KEEPALIVE_CHECK_INTERVAL_MS)
}
static stopKeepaliveTimer(){
(0,T.iM)(this.keepaliveInterval),this.keepaliveInterval=null
}
static enterWakeRecoveryMode(){
K.H8&&(this.wakeRecoveryDeadline=Date.now()+this.WAKE_RECOVERY_WINDOW_MS,i().info(`enterWakeRecoveryMode: IPC resolver timeout extended to ${
this.WAKE_RECOVERY_TIMEOUT_MS
}
ms for ${
this.WAKE_RECOVERY_WINDOW_MS/1e3
}
s`))
}
static handleProcessRestart(){
i().info("HelperAPIClient.handleProcessRestart"),this.pendingData="",this.writeQueue.length>0&&i().warn(`Discarding ${
this.writeQueue.length
}
 queued IPC message(s) — process restart`),this.writeQueue=[],this.bufferHasSpace=!0,this.lastResponseTime=Date.now(),this.lastEventLoopTickTime=Date.now(),this.keepaliveErrorCount=0,this.stopKeepaliveTimer(),this.wakeRecoveryDeadline=0,A.RA.hardwareInfo=null,this.lastObservedTapEnabled=null;
for(const e of[...this.pendingResponses.keys()])this.rejectPendingRequest(e,Ve)
}
static pushData(e){
this.pendingData+=e;
const t=performance.now();
let n=0;
for(;
this.pendingData.includes(Ke);
){
const e=this.pendingData.split(Ke)[0];
this.pendingData=this.pendingData.slice(e.length+1);
const t=this.unescapeMessage(e);
this.handleMessage(t),n++
}
const r=performance.now()-t;
r>100&&n>5&&i().warn(`[IPC] pushData processed ${
n
}
 messages in ${
Math.round(r)
}
ms`)
}
static handleMessage(e){
if(e)try{
const t=e.includes('"evidenceKind"')?JSON.stringify(JSON.parse(e,Xe)):e,n=w.U$.toHelperAPI(t);
n.HelperAPIRequest?this.handleRequest(n.HelperAPIRequest):n.HelperAPIResponse?this.handleResponse(n.HelperAPIResponse):i().error("Unknown HelperAPI message",{
customAttributes:{
message:e
}

}
)
}
catch(t){
i().error("Failed to parse HelperAPI message",{
customAttributes:{
message:e,error:(0,s.serializeError)(t)
}

}
)
}

}
static sendRequest(e,t,n){
const r=this.getMessageType(e);
let s;
t.requestType=r,this.registerResolver(t,e.uuid,n);
try{
const t={
HelperAPIRequest:this.withSentryTraceContext(e)
}
,n=w.U$.helperAPIToJson(t);
s=this.writeMessage(n,r)
}
catch(t){
const n=t instanceof Error?t.name:typeof t;
return i().error("Failed to send request to helper app",{
customAttributes:{
messageType:r,errorName:n
}

}
),void this.rejectPendingRequest(e.uuid,"Failed to serialize or write request")
}
"too_long"!==s?"accepted"!==s||Ye.has(r)||i().info(`Electron -> Helper: ${
r
}
 request sent`):this.rejectPendingRequest(e.uuid,"Request exceeds max IPC message length")
}
static sendRequestAsync(e,t){
return new Promise((n,r)=>{
const i={
resolve:n,reject:r
}
;
this.sendRequest(e,i,t)
}
)
}
static rejectPendingRequest(e,t){
const n=this.pendingResponses.get(e);
n&&(this.pendingResponses.delete(e),(0,T.W3)(n.timeout),n.reject(t,!0))
}
static withSentryTraceContext(e){
const t=(0,Qe.c1)(),n=t["sentry-trace"],r=t.baggage;
return n||r?{
...e,...n&&{
sentryTrace:n
}
,...r&&{
sentryBaggage:r
}

}
:e
}
static escapeMessage(e){
return e.replaceAll(Ze,`${
Ze
}
1`).replaceAll(Ke,`${
Ze
}
2`)
}
static unescapeMessage(e){
return e.replaceAll(`${
Ze
}
2`,Ke).replaceAll(`${
Ze
}
1`,Ze)
}
static sendResponse(e){
const t={
HelperAPIResponse:e
}
,n=w.U$.helperAPIToJson(t),r=this.getMessageType(e);
this.writeMessage(n,r)
}
static writeMessage(e,t){
if(!A.RA.helper.process||!A.RA.helper.process.stdin)return i().error("Attempted to send message to helper app before Helper service was started",{
customAttributes:{
messageType:t
}

}
),"no_transport";
if(A.RA.helper.process.stdin.destroyed)return i().warn("Attempted to send message to helper app after stdin is destroyed",{
customAttributes:{
messageType:t
}

}
),"no_transport";
if(e.length>1e5)return i().error("IPC message is too long, not sending",{
customAttributes:{
messageType:t,messageLength:e.length
}

}
),"too_long";
const n=this.escapeMessage(e).concat(Ke);
if((0,Z.getFeatureFlagData)(S.TO.IsAsyncHelperWriteQueueEnabled).enabled||this.isProcessingWriteQueue)this.writeQueue.push(n),this.isProcessingWriteQueue||(this.isProcessingWriteQueue=!0,this.processWriteQueue());
else{
const e=A.RA.helper.process.stdin.write(n);
e||this.bufferHasSpace||!A.RA.helper.process||A.RA.isShuttingDown||i().error("Writing to helper app stdin buffer while buffer is full"),this.bufferHasSpace=e
}
return"accepted"
}
static async processWriteQueue(){
try{
for(;
this.writeQueue.length>0;
){
const e=A.RA.helper.process?.stdin;
if(!e||e.destroyed){
i().warn(`Discarding ${
this.writeQueue.length
}
 queued IPC message(s) — stdin destroyed`),this.writeQueue=[];
break
}
const t=this.writeQueue[0];
try{
const n=e.write(t);
this.writeQueue.shift(),n||await new Promise(t=>{
let n=null;
const r=()=>{
null!==n&&clearTimeout(n),e.removeListener("drain",r),e.removeListener("error",r),e.removeListener("close",r),t()
}
;
e.once("drain",r),e.once("error",r),e.once("close",r),n=setTimeout(r,this.DRAIN_TIMEOUT_MS)
}
)
}
catch(e){
this.writeQueue.shift(),"EPIPE"===e.code&&A.RA.isShuttingDown?i().warn("Helper process pipe closed while writing message"):A.RA.helper.process?i().error("Error writing to helper process:",{
customAttributes:{
error:e
}

}
):i().warn("Helper process is restarting, cannot write message")
}

}

}
finally{
this.isProcessingWriteQueue=!1
}

}
static metadataKeys=new Set(["uuid","sentryTrace","sentryBaggage"]);
static getMessageType(e){
return Object.entries(e).find(([e,t])=>!this.metadataKeys.has(e)&&void 0!==t)?.[0]
}
static handleRequest(e){
const t=this.getMessageType(e);
if(!t)return void i().error("No valid request type found in data:",{
customAttributes:{
data:e
}

}
);
const n=this.handlers.get(t);
if(n){
const r=performance.now(),s=n(e),o=performance.now()-r;
this.sendResponse(s),o>50&&i().warn(`[IPC] Slow handler: ${
t
}
 took ${
Math.round(o)
}
ms`)
}
else i().error("No handler found for request type",{
customAttributes:{
requestType:t
}

}
);
Ye.has(t)||i().info(`Helper -> Electron: ${
t
}
 handled`)
}
static lastObservedTapEnabled=null;
static observeTapState(e){
if(void 0===e||e===this.lastObservedTapEnabled)return;
const t=this.lastObservedTapEnabled;
if(this.lastObservedTapEnabled=e,null===t&&e)return;
const n={
customAttributes:{
tapEnabled:String(e),previous:null===t?"unknown":String(t)
}

}
;
e?i().info("Helper keyboard tap enabled state changed",n):i().warn("Helper keyboard tap enabled state changed",n)
}
static handleResponse(e){
this.lastResponseTime=Date.now(),this.keepaliveErrorCount=0,this.observeTapState(e.TapState?.payload.tapEnabled);
const t=e.uuid,n=this.pendingResponses.get(t),r=this.getMessageType(e);
if(n){
if(clearTimeout(n.timeout),e.HelperAPIError){
const t=e.HelperAPIError.payload,r=t.params?.code;
if("NO_HANDLER_REGISTERED"===r){
const e=t.params?.messageType??"Unknown";
i().error("Helper has no handler registered for IPC request",{
customAttributes:{
code:r,messageType:e,requestType:n.requestType,description:t.description
}

}
)
}
else i().error("Helper API Error response",{
customAttributes:{
requestType:n.requestType,description:t.description
}

}
);
n.reject(t.description,!0)
}
else Ye.has(r)||i().info(`Helper -> Electron: ${
r
}
 response received`),n.resolve(e);
this.pendingResponses.delete(t)
}
else i().warn(`No resolver found for response with UUID: ${
t
}
`)
}
static registerResolver(e,t,n){
const r=K.H8&&Date.now()<this.wakeRecoveryDeadline,s=r?this.WAKE_RECOVERY_TIMEOUT_MS:this.defaultRequestTimeoutMs,o=n??s;
r&&i().debug(`registerResolver: using extended wake-recovery timeout (${
o
}
ms)`),(0,T.W3)(e.timeout);
const a=setTimeout(()=>{
e.reject($e),this.pendingResponses.delete(t)
}
,o),c={
...e,timeout:a
}
;
this.pendingResponses.set(t,c)
}
static TerminateHelperProcess(e){
try{
(0,He.sendHelperShutdownRequest)()
}
catch(e){
i().warn("Error terminating helper process:",JSON.stringify((0,s.serializeError)(e)))
}
K.H8||e?.kill(),(0,Ge.yG)(e)
}
static async TerminateHelperProcessAndWait(e){
try{
(0,He.sendHelperShutdownRequest)()
}
catch(e){
i().warn("Error terminating helper process:",JSON.stringify((0,s.serializeError)(e)))
}
K.H8||e?.kill(),await(0,Ge.yn)(e)
}

}
function Xe(e,t){
if("MeetingSpeakerChange"!==e||null===t||"object"!=typeof t||!("payload"in t))return t;
const n=t.payload;
if(null===n||"object"!=typeof n||!("evidenceKind"in n))return t;
const{
evidenceKind:r,...i
}
=n,s=(0,I.uz)(i.source);
return void 0!==s&&"zoom_compact"!==s&&"unknown"!==s||("active_speaker_indicator"!==r&&"displayed_participant"!==r?"speakingNames"in i&&(i.speakingNames=null):"zoom_compact"===s&&(i.source="displayed_participant"===r?"zoom_compact_tile":"zoom_compact")),{
...t,payload:i
}

}

}
,68019(e,t,n){
"use strict";
n.d(t,{
VX:()=>p,q0:()=>r
}
);
const r=100,i=/^[a-zA-Z]/,s=/[0-9a-f]{
24,
}
/i,o=/^[A-Za-z0-9]{
40,
}
$/,a=/[a-z][A-Z]/,c=/(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])/,l=/[./\\\-_:@]+/,u=e=>o.test(e)&&(!a.test(e)||(e=>{
const t=e.split(c).map(e=>e.length).sort((e,t)=>e-t);
return t[Math.floor(t.length/2)]
}
)(e)<=2),d=e=>e.includes(".")&&!e.includes(" ")&&i.test(e)?e.length>100?"tooLong":e.includes("://")?"url":s.test(e)?"hashRun":e.split(l).some(u)?"blobStem":null:"notFileShaped",p=e=>{
const t={
kept:[],dropped:{
notFileShaped:0,tooLong:0,url:0,hashRun:0,blobStem:0
}

}
;
for(const n of e){
const e=d(n);
null===e?t.kept.push(n):t.dropped[e]+=1
}
return t
}

}
,2188(e,t,n){
"use strict";
n.d(t,{
w:()=>o
}
);
var r=n(24434),i=n(2203);
let s=-1;
const o=()=>{
const e=new r.EventEmitter,t=e,n=new i.PassThrough,o=new i.PassThrough,a=new i.PassThrough,c=new i.PassThrough;
return Object.assign(e,{
pid:s--,stdin:n,stdout:o,stderr:a,stdio:[n,o,a,c],killed:!1,exitCode:null,signalCode:null,kill:n=>(t.killed=!0,t.signalCode="string"==typeof n?n:null,t.exitCode="number"==typeof n?n:0,e.emit("close",t.exitCode,t.signalCode),!0),ref:()=>e,unref:()=>e
}
),e
}

}
,82319(e,t,n){
"use strict";
n.d(t,{
_g:()=>f,qj:()=>m,zi:()=>h
}
);
var r=n(38e3),i=n(18215),s=n(91922),o=n(28889),a=n(46507),c=n(69583);
const l=(0,n(27946).L)({
failureMessage:"Failed to persist fn analytics to disk"
}
),u=(e,t)=>!0===e.ownerAmbiguous?"ambiguous":(0,s.E0)((0,s.nk)(t,e.ownerBundleId)),d=new Map,p=(e,t)=>h(e,(!0===t.ownerAmbiguous?t.ownerCandidateBundleIds?.join(","):t.ownerBundleId)??"unknown"),h=(e,t)=>{
const n=Date.now(),r=l.get(e),i="object"!=typeof r||null===r||Array.isArray(r)?{

}
:r,s=e=>"number"==typeof e&&e<=n&&n-e<864e5,o=`${
e
}
:${
t
}
`;
if(s(i[t])||s(d.get(o)))return!1;
for(const[e,t]of d)s(t)||d.delete(e);
d.set(o,n);
const a=Object.fromEntries(Object.entries(i).filter(([,e])=>s(e)));
return l.set(e,{
...a,[t]:n
}
),!0
}
,m=(e,t,n)=>{
e.outcome===r.bp.Stolen&&p("fnConflictSentAt",e)&&(0,a.getMainPosthogClient)().sendEvent(i.GW.FnShortcutConflictDetected,{
owner_app:u(e,n),notice_queued:t,fallback_enabled:!0===e.fallbackInjected
}
)
}
,f=(e,t)=>{
if(!p("fnListenerSentAt",e.listenerFound?e:{
ownerBundleId:"none"
}
))return;
const n=(0,o.OP)([o.dK.fn]),r=c.RA.prefs?.user.shortcuts??{

}
;
(0,a.getMainPosthogClient)().sendEvent(i.GW.FnShortcutListenerDetected,{
owner_app:e.listenerFound?u(e,t):"none",flow_shortcut_is_fn:r[n]===o.D.Ptt,granola_installed:e.granolaInstalled
}
)
}

}
,20844(e,t,n){
"use strict";
n.d(t,{
j:()=>l
}
);
var r=n(84157),i=n(69493),s=n.n(i),o=n(48116),a=n(69583),c=n(25292);
const l=()=>o.tD?a.ty.isHelperProcessRunningManually?(s().info("Running Dev Mac Helper service"),`${
c.ZI
}
/swift-helper-app/DerivedData/Wispr Flow Helper/Build/Products/Debug/Wispr Flow.app/Contents/MacOS/Wispr Flow`):(s().info("Running packaged Mac Helper service"),`${
c.ZI
}
/swift-helper-app-dist/Wispr Flow.app/Contents/MacOS/Wispr Flow`):a.ty.isHelperProcessRunningManually||!r.app.isPackaged?(s().info("Running Dev Windows Helper service"),`${
c.ZI
}
\\windows-helper-app\\Wispr Flow Helper\\Release\\Wispr Flow Helper.exe`):(s().info("Running packaged Windows Helper service"),`${
c.ZI
}
\\Release\\Wispr Flow Helper.exe`)
}
,8460(e,t,n){
"use strict";
n.d(t,{
H3:()=>W,Wj:()=>L,XN:()=>j,vw:()=>U
}
);
var r=n(73435),i=