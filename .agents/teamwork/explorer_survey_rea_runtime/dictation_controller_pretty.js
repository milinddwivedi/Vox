18866(e,t,n){
"use strict";
n.d(t,{
NU:()=>De,qd:()=>ke,Kw:()=>be,nB:()=>ge,Qw:()=>Xe,mV:()=>Ge,Pc:()=>Ke,OF:()=>$e,J5:()=>Ye,cr:()=>Ve,$9:()=>Ze,iz:()=>qe,Aj:()=>je,x$:()=>dt,CO:()=>st,US:()=>at,a1:()=>rt,t3:()=>et,x7:()=>Fe
}
);
var r=n(73435),i=n(84157),s=n(69493),o=n.n(s),a=n(71864),c=n(26056),l=n(98139),u=n(59725),d=n(36096),p=n(44753),h=n(11323),m=n(19742);
const f=new Set;
(0,m.J)(()=>f.clear());
const g=(e,t,n)=>{
const r=(0,p.N6)(t),i=(0,p.N6)(n),s=`${
e
}
:${
r
}
:${
i
}
`;
f.has(s)||(f.add(s),o().warn(e,{
customAttributes:{
source:null===t?"local":"served",served:r,local:i
}

}
))
}
;
var b=n(88162),A=n(16452),y=n(60838),v=n(36016),w=n(45961),M=n(66391),_=n(83643),S=n(42935),E=n(46178),O=n(7590),C=n(48116),I=n(89424),k=n(93533),z=n(17826),T=n(28889),N=n(30426),R=n(73747),B=n(19768),x=n(89004),D=n(45332),L=n(35614),P=n(59719),q=n(45391),F=n(50601),W=n(9363),U=n(70234),j=n(41324),Q=n(65045),H=n(36550),G=n(14510),V=n(26181),$=n(44147),K=n(17059),Z=n(4510),Y=n(37187),J=n(80960),X=n(40789),ee=n(4157),te=n(41909),ne=n(15769),re=n(68149),ie=n(61230),se=n(41576),oe=n(86816),ae=n(69583),ce=n(70307),le=n(8729),ue=n(92494),de=n(1810),pe=n(59625),he=n(23345),me=n(63364),fe=n(72413);
const ge=()=>ae.RA.statusWindow||(o().warn("[StateManager] Bubble window is not available - statusWindow is null"),null),be=()=>{
A.ZZ.isInstructMode&&(A.ZZ.isInstructMode=!1,A.ZZ.instructModeTransitionTime=null,(0,X.Bn)(ge(),z.LF.InstructModeStatus,!1))
}
,Ae=()=>!ae.RA.prefs?.user.onboardingCompleted&&((0,re.getFeatureFlagData)(k.TO.TIYOnboardingToasts).variant===k.$7.Test&&((0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)||(0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.gL))),ye=(e,t)=>{
(0,X.Bn)(ae.RA.hubWindow,z.SF.ShowTIYToast,{
type:e,originalNotification:t
}
)
}
,ve=new Set([B.Eg.TranscriptionError,B.Eg.NetworkPolicyBlocked,B.Eg.ProcessingDelayNotification,B.Eg.TranscriptCurrentlyProcessing,B.Eg.NoInternet,B.Eg.HelperNotReady,B.Eg.HelperPersistentFailure,B.Eg.SignedOut,B.Eg.NoAudioDuringDictation]),we=e=>{
Ae()&&ve.has(e)?ye("oops",e):e!==B.Eg.TranscriptCurrentlyProcessing?(0,te.sendNotification)(e):(0,te.sendTranscriptCurrentlyProcessingNotification)()
}
;
let Me=null,_e=null,Se=null,Ee=null,Oe=null,Ce=null,Ie=null;
const ke=()=>{
(0,P.W3)(Ce),(0,te.invalidateLongDictationPrompt)()
}
,ze=new Set([B.Eg.PostOnboardingInAppDictation,B.Eg.DictatingWithWisprFlow,B.Eg.MicSwitched,B.Eg.AirPodsWarning]),Te=new Map,Ne=(e,t=()=>(0,te.sendNotification)(e))=>{
if(!ze.has(e))return void t();
const n=(0,re.getFeatureFlagData)(k.TO.MeetingRecorder);
(0,k.v9)({
payload:n.payload,userPrefEnabled:ae.RA.prefs?.user.notetakerTripleTapShortcutEnabled??!0,notetakerOrgDisabled:(0,R.QL)(ae.RA.prefs)
}
)?((0,P.W3)(Te.get(e)??null),Te.set(e,setTimeout(()=>{
Te.delete(e),t()
}
,u.DICTATION_DEBOUNCE_DELAY))):t()
}
;
let Re=0;
const Be=[I._W.Stopping,I._W.Processing],xe=C.HN+2,De=()=>{
ae.RA.prefs?.activeDictationSession&&(0,ie.$y)({
activeDictationSession:null
}
)
}
,Le=()=>{
(0,P.W3)(Se),Se=null
}
,Pe=e=>{
Se=setTimeout(()=>{
Se=null,o().warn("Processing timeout reached, setting status to Error",{
customAttributes:{
uuid:A.ZZ.uuid,timeoutMs:e
}

}
),(0,M.VT)({
lifecycle:{
result:"failure",reason:"processing_timeout"
}
,transaction:{
status:"error",attributes:{
"dictation.status":"error","dictation.failure_mode":"processing_timeout"
}

}

}
),Fe(I._W.Error),(0,X.Bn)(ae.RA.hubWindow,z.lM.NeedsUpdate),A.ZZ.timedOut=!0
}
,e)
}
,qe=e=>Se&&A.ZZ.uuid===e?(Le(),Pe(C.rn),!0):(o().info("Not refreshing the processing timeout",{
customAttributes:{
ownerUuid:e,liveUuid:A.ZZ.uuid,armed:null!==Se
}

}
),!1),Fe=(e,t=!0,n={

}
)=>{
if(ae.RA.prefs?.activeDictationSession&&I.KB.includes(e)){
o().warn(`[dictation] activeDictationSession sentinel still set on terminal status transition to '${
e
}
'. This may indicate a missing clearing location.`,{
uuid:ae.RA.prefs.activeDictationSession.uuid,estimated_duration_ms:Date.now()-ae.RA.prefs.activeDictationSession.startTimestamp
}
);
try{
De()
}
catch(e){
o().warn("Failed to clear dictation sentinel in status transition fail-safe",{
customAttributes:{
uuid:A.ZZ.uuid,error:(0,a.serializeError)(e)
}

}
)
}

}
o().info(`updateDictationStatus: ${
e
}
`,{
customAttributes:{
uuid:A.ZZ.uuid
}

}
),e===I._W.Error&&o().warn("Dictation status transitioning to Error",{
customAttributes:{
uuid:A.ZZ.uuid,previousStatus:A.ZZ.status
}

}
),Le(),(0,P.W3)(Ee);
const r=ge();
n.skipBubbleIPC?o().info("updateDictationStatus: skipping bubble DictationStatus IPC (route owns bubble indicator)",{
customAttributes:{
uuid:A.ZZ.uuid,status:e
}

}
):(0,X.Bn)(r,z.LF.DictationStatus,e),(0,X.Bn)(ae.RA.hubWindow,z.LF.DictationStatus,e);
const i=A.ZZ.status;
A.ZZ.status=e,e===I._W.Dismissed&&(A.ZZ.wasDismissed=!0),A.ZZ.statusLastUpdatedTime=Date.now();
const s=(0,ce.BV)().instructChat;
if((s.open||null!==s.session)&&(A.ZZ.isInstructMode||null===s.activeTool)){
const t=(0,K.Wl)(e),n="processing"===s.dictationPhase&&"transcribing"===t?"processing":t;
s.dictationPhase!==n&&((0,ce.jF)({
dictationPhase:n,activeTool:null
}
),(0,$.uI)())
}
(0,y.kX)(e),I.KB.includes(e)&&!I.KB.includes(i)&&ae.RA.extensionSystem?.broadcastDictationEnd(),A.ZZ.matchedRouteName||A.ZZ.isInstructMode||(Be.includes(e)&&(Pe(C.rn),Ee=setTimeout(()=>{
ae.RA.prefs?.user.processingDelayNotificationEnabled&&!A.ZZ.isInstructMode&&we(B.Eg.ProcessingDelayNotification)
}
,4e3)),e===I._W.Error&&t&&we(n.errorNotification??B.Eg.TranscriptionError))
}
,We=()=>I.cD.includes(A.ZZ.status),Ue=(e,t)=>{
const n=new Set(t),r=new Set(e);
return r.size===n.size&&[...r].every(e=>n.has(e))
}
,je=(e,t,n,r)=>{
if(!I.B8.includes(A.ZZ.status))return;
const i=A.ZZ.triggerShortcut,s=i?.startsWith(T.jE)??!1,o=s?i?.slice(T.jE.length):i,a=A.ZZ.triggerKeycodes&&r?Ue(A.ZZ.triggerKeycodes,r):o===t;
(void 0===t||a)&&(A.ZZ.triggerShortcut=s?(0,T.RD)(e):e,n&&(A.ZZ.triggerKeycodes=n))
}
,Qe=(e,t)=>{
((e,t)=>{
const n=A.ZZ.triggerShortcut;
if(!n)return!1;
if(A.ZZ.triggerKeycodes&&t){
const e=new Set(A.ZZ.triggerKeycodes),n=new Set(t);
return e.size<n.size&&[...e].every(e=>n.has(e))
}
const r=(n.startsWith(T.jE)?n.slice(T.jE.length):n).split("+"),i=new Set(e.split("+"));
return r.length<i.size&&r.every(e=>i.has(e))
}
)(e,t)&&je(e,void 0,t)
}
,He=(e,t)=>{
const n=A.ZZ.triggerShortcut,r=n?.startsWith(T.jE)?n?.slice(T.jE.length):n,i=A.ZZ.triggerKeycodes&&t?Ue(A.ZZ.triggerKeycodes,t):e===r;
return null==r||void 0===e||i?(0,T.RD)(n??e??null):n
}
,Ge=(e=I.SB.Keyboard,t,n)=>{
const{
popoTestingMode:r
}
=(0,ce.BV)();
if(A.ZZ.status===I._W.Testing)return void o().info("Action down ignored - currently in mic testing mode");
if(We()&&A.ZZ.status!==I._W.Initializing)return o().warn("User pressed action down while the previous transcription is still processing"),void(Date.now()-me.tL.lastActionTime>u.DICTATION_DEBOUNCE_DELAY&&we(B.Eg.TranscriptCurrentlyProcessing));
if(!(0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)&&!ae.RA.prefs?.user.onboardingCompleted)return void o().info("Action down but onboarding not complete");
o().info(`Ptt action down. Dictation status: ${
A.ZZ.status
}
`);
const i=A.ZZ.isCommandMode;
if(A.ZZ.isCommandMode=!1,A.ZZ.isInstructMode=!1,A.ZZ.status!==I._W.Listening&&A.ZZ.status!==I._W.Initializing)(0,b.Ym)(!1),st(e,t,n);
else if((new Date).getTime()-A.ZZ.startTime<u.DICTATION_DEBOUNCE_DELAY&&!i)if(A.ZZ.isLocked)o().info("Action dismiss on quick triple action press"),et(I.W5.DebounceTriplePress);
else{
if(!ae.RA.prefs?.user.onboardingCompleted&&!r&&!(0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.S$))return;
o().info("Action down twice in quick succession --\x3e lock"),(0,b.Ym)(!0),(0,v.eJ)(),A.ZZ.triggerShortcut=He(t,n),(0,X.Bn)(ae.RA.statusWindow,z.LF.POPO),(r||!ae.RA.prefs?.user.onboardingCompleted&&(0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.S$))&&(0,X.Bn)(ae.RA.hubWindow,z.LF.POPO),ke()
}
else A.ZZ.isLocked&&at(I.SB.Keyboard,"re_press")
}
,Ve=(e,t)=>{
if(o().info("Wispr Lens down"),(0,D.SG)(ae.RA.prefs?.user.subscription,ae.RA.prefs?.user.enterprise?.subscription)){
if(!ae.RA.prefs?.user.commandModeEnabled)return o().info("Wispr Lens down called, but command mode is disabled in preferences, ignoring"),void(0,te.sendNotification)(B.Eg.CommandModeDisabled);
if((0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)||ae.RA.prefs?.user.onboardingCompleted){
if(o().info(`Wispr Lens action down. Dictation status: ${
A.ZZ.status
}
, isLocked: ${
A.ZZ.isLocked
}
`),We()&&A.ZZ.status!==I._W.Initializing)return o().warn("User pressed Wispr Lens while the previous transcription is still processing"),void(Date.now()-me.tL.lastActionTime>u.DICTATION_DEBOUNCE_DELAY&&we(B.Eg.TranscriptCurrentlyProcessing));
if(!A.ZZ.isLocked){
A.ZZ.isCommandMode=!0,A.ZZ.isInstructMode=!1,(0,v.eJ)();
const e=ge();
(0,X.Bn)(e,z.LF.CommandModeStatus,!0),(0,te.isNotificationEligible)(B.Eg.FirstTimeUsingCommandMode)?(0,te.sendNotification)(B.Eg.FirstTimeUsingCommandMode):(0,te.sendNotification)(B.Eg.UsingCommandMode)
}
A.ZZ.status!==I._W.Listening&&A.ZZ.status!==I._W.Initializing?((0,b.Ym)(!1),st(I.SB.Keyboard,e,t)):e&&Qe(e,t)
}
else o().info("Action down but onboarding not complete")
}
else o().error("Wispr Lens down called by non-paying user")
}
,$e=(e,t)=>{
if(o().info("Instruct mode down"),!(0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)&&!ae.RA.prefs?.user.onboardingCompleted)return void o().info("Action down but onboarding not complete");
o().info(`Instruct action down. Dictation status: ${
A.ZZ.status
}
, isLocked: ${
A.ZZ.isLocked
}
`);
const n=(0,J.v)();
if(n?J.A.prefetch(n):o().debug("[UserVoicePreference] skipping prefetch, app identity not resolved yet"),We()&&A.ZZ.status!==I._W.Initializing)return o().warn("User pressed Instruct while the previous transcription is still processing"),void(Date.now()-me.tL.lastActionTime>u.DICTATION_DEBOUNCE_DELAY&&we(B.Eg.TranscriptCurrentlyProcessing));
const r=Date.now()-Re;
Re=Date.now();
const i=A.ZZ.isInstructMode;
if(!A.ZZ.isLocked){
const n=I.B8.includes(A.ZZ.status)&&!A.ZZ.isInstructMode,r=Date.now()-A.ZZ.startTime;
if(n&&(r<0||r>u.MID_DICTATION_INSTRUCT_GRACE_MS))o().info("Blocking dictation->instruct switch (mid-dictation)",{
customAttributes:{
elapsedSinceStart:r
}

}
);
else{
A.ZZ.isInstructMode=!0,A.ZZ.isCommandMode=!1,(0,v.eJ)(),n&&(A.ZZ.instructModeTransitionTime=Date.now(),e&&Qe(e,t),(0,U.switchToVoiceActionsRealtimeMidSession)()),(0,re.trackPosthogEvent)("instruct_mode_activated",{
source:"keyboard",transitioned_from_dictation:n,transition_delay_seconds:n?(Date.now()-A.ZZ.startTime)/1e3:null
}
);
const r=ge();
(0,X.Bn)(r,z.LF.InstructModeStatus,!0)
}

}
if(A.ZZ.status!==I._W.Listening&&A.ZZ.status!==I._W.Initializing)(0,b.Ym)(!1),st(I.SB.Keyboard,e,t);
else if(i&&r<u.DICTATION_DEBOUNCE_DELAY)if(A.ZZ.isLocked)o().info("Instructions dismiss on quick triple press"),et(I.W5.DebounceTriplePress);
else{
o().info("Instruct double-tap detected --\x3e POPO lock"),(0,b.Ym)(!0),(0,v.eJ)(),A.ZZ.triggerShortcut=He(e,t);
const n=ge();
(0,X.Bn)(n,z.LF.POPO),(0,P.W3)(Oe),ke()
}
else A.ZZ.isLocked&&at(I.SB.Keyboard,"re_press")
}
,Ke=(e=I.SB.Keyboard)=>{
((0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)||ae.RA.prefs?.user.onboardingCompleted)&&(o().info(`Action up. Dictation status: ${
A.ZZ.status
}
, POPO: ${
A.ZZ.isLocked
}
`),A.ZZ.status!==I._W.Initializing||A.ZZ.isLocked?A.ZZ.status!==I._W.Listening||A.ZZ.isLocked?A.ZZ.status!==I._W.Listening?o().warn("Action up but dictation is not listening"):o().info("Action up but dictation is locked"):(new Date).getTime()-A.ZZ.startTime<u.DICTATION_DEBOUNCE_DELAY?tt():(ke(),at(e,"key_release")):Date.now()-A.ZZ.startTime<u.DICTATION_DEBOUNCE_DELAY?tt():et(I.W5.DebouncePTT))
}
,Ze=()=>{
((0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)||ae.RA.prefs?.user.onboardingCompleted)&&(o().info(`Wispr Lens action up. Dictation status: ${
A.ZZ.status
}
`),A.ZZ.status!==I._W.Listening||A.ZZ.isLocked?A.ZZ.status!==I._W.Initializing||A.ZZ.isLocked?A.ZZ.status!==I._W.Listening?o().info("Wispr Lens action up but dictation is not listening"):o().info("Wispr Lens action up but dictation is locked"):et(I.W5.DebouncePTT):at(I.SB.Keyboard,"key_release"))
}
,Ye=()=>{
((0,x.P$)(ae.RA.prefs?.user.pagesCompleted,I.lz)||ae.RA.prefs?.user.onboardingCompleted)&&(o().info(`Instruct action up. Dictation status: ${
A.ZZ.status
}
, POPO: ${
A.ZZ.isLocked
}
`),A.ZZ.status!==I._W.Initializing||A.ZZ.isLocked?A.ZZ.status!==I._W.Listening||A.ZZ.isLocked?A.ZZ.status!==I._W.Listening&&A.ZZ.status!==I._W.Initializing?o().info("Instruct action up but dictation is not listening"):o().info("Instruct action up but dictation is locked"):(new Date).getTime()-A.ZZ.startTime<u.DICTATION_DEBOUNCE_DELAY?tt():(ke(),at(I.SB.Keyboard,"key_release")):Date.now()-A.ZZ.startTime<u.DICTATION_DEBOUNCE_DELAY?tt():et(I.W5.DebouncePTT))
}
,Je=()=>{
Re=0
}
,Xe=(e,t,n)=>{
if(A.ZZ.status===I._W.Testing)return void o().info("Action click ignored - currently in mic testing mode");
if(We()&&A.ZZ.status!==I._W.Initializing)return o().warn("User tried to activate POPO while the previous transcription is still processing"),void(Date.now()-me.tL.lastActionTime>u.DICTATION_DEBOUNCE_DELAY&&we(B.Eg.TranscriptCurrentlyProcessing));
if(I.B8.includes(A.ZZ.status)&&(A.ZZ.isLocked||e===I.SB.StatusIndicator))return void(A.ZZ.isLocked&&Date.now()-A.ZZ.startTime<u.DICTATION_DEBOUNCE_DELAY?(o().info("POPO dismiss on quick double press"),et(I.W5.DebouncePOPO)):at(e,e===I.SB.StatusIndicator?"stop_button":"re_press"));
I.B8.includes(A.ZZ.status)||(o().info(`Popo action down. Dictation status: ${
A.ZZ.status
}
`),st(e,t,n)),A.ZZ.isCommandMode=!1,A.ZZ.isInstructMode=!1;
const r=ge();
(0,X.Bn)(r,z.LF.CommandModeStatus,!1),(0,X.Bn)(r,z.LF.InstructModeStatus,!1),t&&Qe(t,n),(0,b.Ym)(!0),(0,v.eJ)(),ke(),(0,X.Bn)(r,z.LF.POPO)
}
,et=e=>{
const t=A.ZZ.status;
if(A.ZZ.isCommandMode){
const e=ge();
(0,X.Bn)(e,z.LF.CommandModeStatus,!1)
}
if(A.ZZ.isInstructMode){
const e=ge();
(0,X.Bn)(e,z.LF.InstructModeStatus,!1)
}
try{
De()
}
catch(e){
o().warn("Failed to clear dictation sentinel in tryDismissDictation",{
customAttributes:{
uuid:A.ZZ.uuid,error:(0,a.serializeError)(e)
}

}
)
}
if(Fe(I._W.Dismissed),t===I._W.Testing&&(0,X.Bn)(ae.RA.hubWindow,z.tv.CloseMicrophoneDialog),e===I.W5.DebounceTriplePress){
for(const[e,t]of Te)(0,P.W3)(t),(0,te.clearActiveNotification)({
type:e,reason:"override"
}
);
Te.clear()
}
[I._W.Initializing,I._W.Listening,I._W.Stopping,I._W.Processing].includes(t)&&lt(e),ke(),Je()
}
,tt=()=>{
(0,P.W3)(Oe),Oe=setTimeout(()=>{
A.ZZ.status!==I._W.Listening&&A.ZZ.status!==I._W.Initializing||A.ZZ.isLocked||(o().info("PTT stopping after timeout waiting for double press"),et(I.W5.DebouncePTT))
}
,u.DICTATION_DEBOUNCE_DELAY)
}
,nt=()=>{
(0,te.sendDebounceNotification)()
}
,rt=(e,t=!1,n)=>{
o().info(`stopListening dictation source: ${
e
}
`),t?(0,X.Bn)(ae.RA.hubWindow,z.kH.AudioStop,{
measurePostRelease:!0,...void 0!==n&&{
quietBelowDb:n
}

}
):e!==I.SB.BLE&&(0,X.Bn)(ae.RA.hubWindow,z.kH.AudioStop),(0,se.ui)(!1)
}
,it={
[I.SB.StatusIndicator]:T.t1,[I.SB.Deeplink]:T.BG,[I.SB.BLE]:T.k5
}
,st=(e,t,n)=>{
if(A.ZZ.status===I._W.Stopping)return o().warn("startDictation called while the previous dictation is still stopping"),void(0,d.BD)("already_stopping");
if(ae.RA.prefs?.activeDictationSession){
(0,d.fF)(ae.RA.prefs.activeDictationSession,"new_dictation_start");
try{
De()
}
catch(e){
o().warn("Failed to clear dictation sentinel in startDictation",{
customAttributes:{
uuid:A.ZZ.uuid,error:(0,a.serializeError)(e)
}

}
)
}

}
if((0,oe.YQ)(),o().info(`startDictation. Dictation status: ${
A.ZZ.status
}
. Action source: ${
e
}
`),me.tL.lastActionTime=Date.now(),A.ZZ.status===I._W.Initializing)return o().warn("startDictation called while a dictation is already being initialized"),void(0,d.BD)("already_initializing");
if(A.ZZ.actionSource=e,A.ZZ.triggerShortcut=t??it[e]??T.H,A.ZZ.triggerKeycodes=n??null,!((e,t)=>{
const n=(0,p.F4)(e,p.wy.Dictation),r=!(t===h.iv.ItAdmin);
null!==n&&n!==r&&(0,d.Bt)(n?"true":"false");
const i=!0===n||r;
return i?!1===n&&g("[Access] served dictation deny ignored",n,r):g("[Access] dictation blocked",n,r),i
}
)(ae.RA.prefs?.cache?.accessFlags,ae.RA.prefs?.user.enterprise?.userRole))return void(0,d.BD)("it_admin_blocked");
if(A.ZZ.status===I._W.Testing)return o().warn("startDictation called with dictation state testing"),void(0,d.BD)("testing_mode");
if(j.e.getDictionaryContext().then(e=>{
A.ZZ.dictionaryContext=e.phrases,A.ZZ.starredDictionaryContext=e.starredPhrases,A.ZZ.teamDictionaryContext=e.teamPhrases,A.ZZ.starredTeamDictionaryContext=e.starredTeamPhrases
}
),ae.RA.prefs?.user.postOnboardingCompleted||!ae.RA.prefs?.user.onboardingCompletedTime||ae.RA.prefs?.user.onboardingReentryActive||(0,V.getAppInfo)().then(e=>{
const t=e.bundleId;
(0,u.isFlowApp)(t)&&Ne(B.Eg.PostOnboardingInAppDictation)
}
).catch(e=>{
o().warn("[POST ONBOARDING]: Error checking app info:",e)
}
),"true"!==process.env.E2E_TEST&&(!ae.RA.helper.ready||null===ae.RA.helper.process))return o().error("Helper was not ready at startDictation"),(0,d.BD)("helper_not_ready"),we(B.Eg.HelperNotReady),(0,P.W3)(Ie),void(Ie=setTimeout(()=>{
ae.RA.helper.ready&&null!==ae.RA.helper.process?o().info("Helper became ready after initial notification"):(o().error("Helper still not ready after timeout - showing persistent failure notification"),(0,d.BD)("helper_persistent_failure"),we(B.Eg.HelperPersistentFailure)),Ie=null
}
,xe));
if(!ae.RA.authSignedIn)return we(B.Eg.SignedOut),void(0,d.BD)("not_signed_in");
Fe(I._W.Initializing);
const s=A.ZZ.uuid;
if(e!==I.SB.BLE&&(0,ne.TB)(!1).then(e=>{
if(!e){
if(A.ZZ.uuid!==s||!I.B8.includes(A.ZZ.status))return;
return at(I.SB.Error,"error"),Fe(I._W.Error,!1),void(0,M.VT)({
lifecycle:{
result:"failure",reason:"mic_permission_denied"
}
,transaction:{
status:"error",attributes:{
"dictation.status":"mic_permission_denied","dictation.failure_mode":"mic_permission_denied"
}

}

}
)
}

}
),(0,F.iD)().then(e=>{
if(!e)return o().warn("User does not have a valid access token on dictation start. Forcing re-auth."),void(0,F.NP)()
}
),ae.RA.prefs?.user.email&&ae.RA.prefs?.user.email.endsWith("@ramp.com")&&"ramp.com"!==ae.RA.prefs?.user.enterprise?.domain)return(0,te.sendNotification)(B.Eg.RampOrphanBlocked),void(0,d.BD)("enterprise_blocked");
if((0,ue.$9)(k.TO.DomainCapture)&&ae.RA.prefs?.user.email&&null==ae.RA.prefs?.user.enterprise&&ae.RA.domainInfo?.domain_capture===h.De.Require){
const e=ae.RA.domainInfo.team_name??"",t=ae.RA.prefs.user.email.split("@")[1]??"";
return(0,te.sendDomainCaptureRequiredBlockedNotification)(e,t),void(0,d.BD)("domain_capture_enterprise_blocked")
}
if(!(0,D.SG)(ae.RA.prefs?.user.subscription,ae.RA.prefs?.user.enterprise?.subscription)&&(ae.RA.prefs?.cache.statistics.wordsThisWeek||0)>(0,L.mX)(ae.RA.prefs?.user.bonusEndsAt))return(0,te.sendNotification)((0,le.Lk)()?B.Eg.ClaimStudentPlan:B.Eg.WeeklyWordsLimitReached),H.z.fetchStats(),void(0,d.BD)("weekly_word_limit");
(0,w.CB)(),(0,te.keepsConflictCard)()||(0,te.clearActiveNotification)({
reason:"override"
}
);
const c="true"!==process.env.E2E_TEST&&!i.net.isOnline();
c&&(we(B.Eg.NoInternet),(0,d.vB)()),(0,A.f4)(),A.ZZ.offlineAtStart=c,q.ActivationNotificationManager.dismissNotification(O.tY.FirstDictation);
const m=crypto.randomUUID();
(0,M.OA)(m,e),(0,M.q8)("dictation.offline_at_start",c);
const f=(0,he.I7)();
var b;
if(null!==f&&(0,M.q8)("dictation.ms_since_wake",f),A.ZZ.uuid=m,(0,te.invalidateLongDictationPrompt)(),b=m,(0,ie.$y)({
activeDictationSession:{
uuid:b,startTimestamp:Date.now()
}

}
),A.ZZ.startTime=Date.now(),o().info(`============================== Start ${
A.ZZ.uuid
}
 ==============================`),(0,l._K)()||(0,l.c9)()){
const e=m;
(0,l.vB)().then(t=>((e,t)=>{
e.blocked&&((e=>{
A.ZZ.uuid===e&&I.wJ.includes(A.ZZ.status)&&(o().info("Dictation blocked by enterprise app deny-list;
 aborting",{
customAttributes:{
uuid:e
}

}
),et(I.W5.AdminPolicyBlocked))
}
)(t),(0,l.nF)("start",e))
}
)(t,e)).catch(t=>{
o().warn("App deny-list start gate failed;
 failing open",{
customAttributes:{
uuid:e,error:String(t)
}

}
)
}
)
}
(0,v.eJ)();
const y=(0,U.voiceActionsRealtimeEligibility)();
var S,R,x;
A.ZZ.useOpus=(S=(0,re.getFeatureFlagData)(k.TO.OpusDesktopExperiment),R=(0,re.getFeatureFlagData)(k.TO.UseOpus),x=y.use_voice_actions_realtime,!!(0,G.Ki)()&&(S.enabled&&S.variant?S.variant===k.nU.Test:R.enabled)&&!x),A.ZZ.useOpusIntended=A.ZZ.useOpus,A.ZZ.vaRealtimeEligible=y.use_voice_actions_realtime,(0,re.fireOpusExposureOnce)(),(0,re.fireFnHardwareFallbackExposureOnce)(),ee.activeMeetingSession.pauseMicForDictation(),(e=>{
o().info(`AudioStart IPC startListening dictation source: ${
e
}
, sent at ${
Date.now()
}
`),A.ZZ.audioAcquireSpan=(0,M.b4)("dictation.audio_acquire","dictation.audio_acquire")??null,e!==I.SB.BLE&&(0,X.Bn)(ae.RA.hubWindow,z.kH.AudioStart,{
useOpus:A.ZZ.useOpus,transcriptionEntityUuid:A.ZZ.uuid
}
),(0,se.ui)(!0)
}
)(e),e===I.SB.BLE&&Fe(I._W.Listening),ae.RA.extensionSystem?.broadcastDictationStart(),ae.RA.prefs?.user.creatorMode&&Ne(B.Eg.DictatingWithWisprFlow),(0,V.sendHelperRequest)("DictationStart",{
includeOffscreen:A.ZZ.isInstructMode,captureMarkerContext:ae.RA.prefs?.user.internal?.shouldSaveAxText??!1
}
),e===I.SB.BLE&&((0,_.W)(),(0,V.sendHelperRequest)("RecordingStarted")),(0,Z.o)();
const $=(0,M.b4)("dictation.streaming_connect","dictation.connect");
try{
if(o().info("[VoiceActionsRealtime] streaming backend selection",{
customAttributes:{
...y,selected_backend:y.use_voice_actions_realtime?"grpc+voice_actions_realtime":"grpc"
}

}
),y.use_voice_actions_realtime)W.A.connect(),U.VoiceActionsRealtimeClient.connect();
else{
const e=(0,Y.Xq)();
e&&(0,Y.kY)(e,E.zMt.Cancelled),U.VoiceActionsRealtimeClient.close(),W.A.connect()
}
$?.end()
}
catch(e){
const t=JSON.stringify((0,a.serializeError)(e));
o().error("Error connecting to streaming service",{
customAttributes:{
error:t
}

}
),$?.setStatus({
code:r.TJ
}
),$?.end(),A.ZZ.fallbackReason=A.Dq.ConnectionError,A.ZZ.fallbackErrorMessage=t
}
(0,de.L)(A.ZZ.uuid),(0,P.W3)(Me),Me=setTimeout(()=>{
A.ZZ.status===I._W.Listening&&(o().info("Dictation will stop after one more minute"),(0,te.sendNotification)(B.Eg.DictationDurationWarning))
}
,u.DICTATION_DURATION_WARNING_TIMEOUT),(0,P.W3)(_e),_e=setTimeout(()=>{
A.ZZ.status===I._W.Listening&&(o().info("Dictation stopping after timeout waiting for duration"),A.ZZ.forceStopped="max_duration",at(I.SB.Keyboard,"max_duration"),(0,te.sendNotification)(B.Eg.DictationMaxDurationReached))
}
,u.DICTATION_DURATION_TIMEOUT);
const K=new Date;
Q.HistoryManager.updateItem(A.ZZ.uuid,{
timestamp:K,timezoneOffsetMinutes:K.getTimezoneOffset()
}
),j.e.getDictionaryContext().then(e=>{
A.ZZ.dictionaryContext=e.phrases,A.ZZ.starredDictionaryContext=e.starredPhrases,A.ZZ.teamDictionaryContext=e.teamPhrases,A.ZZ.starredTeamDictionaryContext=e.starredTeamPhrases
}
);
const J=A.ZZ.micSwitchedToName;
J&&(A.ZZ.micSwitchedToName=null,ae.RA.prefs?.user.postOnboardingCompleted&&Ne(B.Eg.MicSwitched,()=>(0,te.sendMicSwitchedNotification)(J,{
onRefused:e=>{
"slot_busy"===e&&(A.ZZ.micSwitchedToName??=J)
}

}
)));
const ce=ae.RA.sharedSettings.audioDevices.find(e=>e.selected);
if(ce&&(0,N.XP)(ce)&&Ne(B.Eg.AirPodsWarning),C.tD&&(0,N._X)(ae.RA.sharedSettings.audioDevices)){
const e=ae.RA.hardwareInfo?.isClamshellMode??!1,t=A.ZZ.uuid;
(0,V.fetchHardwareInfo)().then(n=>{
A.ZZ.uuid===t&&I.B8.includes(A.ZZ.status)&&(o().info("Clamshell check at dictation start",{
customAttributes:{
cachedIsClamshell:String(e),newIsClamshell:String(n.isClamshellMode)
}

}
),n.isClamshellMode&&(0,te.sendNotification)(B.Eg.NoClamshellBuiltInMic))
}
)
}
ot()
}
,ot=(e=C.H8)=>{
const t=ae.RA.statusWindow;
if(!t||t.isDestroyed())return o().error("Status window is not available or destroyed. Recreating."),void(0,fe.SB)();
const n=t.isAlwaysOnTop(),r=t.isVisible(),i=()=>{
if(e)return t.showInactive(),void t.setAlwaysOnTop(!0,"screen-saver");
t.setAlwaysOnTop(!0,"screen-saver"),t.showInactive()
}
;
if(n&&r)e&&i();
else{
i();
const s=t.isVisible(),a=((e,t,n,r)=>!r&&e&&!t&&n)(n,r,s,e);
pe.qd.count("dictation.status_window_visibility_drift",1,{
attributes:{
outcome:a?"recovered":"unconfirmed"
}

}
);
const c={
wasVisible:r,wasAlwaysOnTop:n,isVisibleAfterReshow:s
}
;
a?o().warn("Status window not visible at dictation start, re-showed successfully",{
customAttributes:c
}
):o().error("Status window not visible",{
customAttributes:c
}
)
}
(0,fe.i6)()
}
,at=(e,t="unclassified")=>{
if(o().info(`stopDictation. Dictation status: ${
A.ZZ.status
}
. Action source: ${
e
}
`),me.tL.lastActionTime=Date.now(),!I.B8.includes(A.ZZ.status))return void o().warn(`stopDictation called when no recording is starting or active. Status is ${
A.ZZ.status
}
. Ignoring.`);
const n=A.ZZ.isLocked,r=A.ZZ.startTime,i=A.ZZ.uuid;
Fe(I._W.Stopping);
let s,a=!1;
(0,S.bR)()&&((0,S.MV)(t),a="key_release"===t&&!ee.activeMeetingSession.isActive(),a&&((0,S.V$)(),s=(0,S.ci)(A.ZZ.wavAudioPackets.volumes.slice(A.ZZ.syntheticWavPacketCount)),(0,S.TQ)({
stop_post_quiet_db:s
}
))),rt(e,a,s),(0,b.Ym)(!1),Je(),(0,V.sendHelperRequest)("DictationStop"),(0,P.W3)(Me),(0,P.W3)(_e),(0,P.W3)(Ie),ee.activeMeetingSession.resumeMicAfterDictation(),r>0&&((e,t,n)=>{
ke(),e<6e4||t||(e=>{
(0,te.isNotificationEligible)(B.Eg.SuggestPOPO)&&(Ce=setTimeout(()=>{
A.ZZ.uuid&&A.ZZ.uuid!==e||I.qG.has(A.ZZ.status)||(0,te.isNotificationEligible)(B.Eg.SuggestPOPO)&&(0,te.sendSuggestPOPONotification)()
}
,3e3))
}
)(n)
}
)(Date.now()-r,n,i)
}
,ct=new Set([I.W5.ExcessiveDurationError,I.W5.ForceReauth]),lt=e=>{
const t=W.A.isConnected(),n=W.A.hasSentCommit(),r=A.ZZ.startTime?Date.now()-A.ZZ.startTime:void 0,i=ct.has(e);
if((0,S.bR)()&&(0,S.TQ)({
stop_source:"dismiss"
}
),(0