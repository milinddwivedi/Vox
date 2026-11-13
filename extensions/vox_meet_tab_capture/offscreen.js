// VOX Offscreen Tab Capture Worker
const BRIDGE_URL = "http://127.0.0.1:49231";
let mediaStream = null;
let audioContext = null;
let frameTimer = null;
let activeMeta = null;

async function startCapture(msg) {
  stopCapture();
  activeMeta = {
    tabId: msg.tabId,
    title: msg.title || "",
    url: msg.url || "",
    meetingSlug: msg.meetingSlug || ""
  };

  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: msg.streamId
      }
    },
    video: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: msg.streamId,
        maxWidth: 1920,
        maxHeight: 1080,
        maxFrameRate: 24
      }
    }
  });

  // Continue playing tab audio to the user's speakers so the call stays audible
  audioContext = new AudioContext();
  const source = audioContext.createMediaStreamSource(mediaStream);
  source.connect(audioContext.destination);

  const video = document.getElementById("captureVideo");
  const canvas = document.getElementById("captureCanvas");
  const ctx = canvas.getContext("2d", { alpha: false });
  video.srcObject = mediaStream;
  await video.play();

  frameTimer = setInterval(async () => {
    if (!video.videoWidth || !video.videoHeight || !activeMeta) {
      return;
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.86);
    try {
      await fetch(`${BRIDGE_URL}/frame`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tab_id: activeMeta.tabId,
          width: canvas.width,
          height: canvas.height,
          title: activeMeta.title,
          url: activeMeta.url,
          frame_b64: dataUrl
        })
      });
    } catch (err) {
      // Ignore transient bridge errors when VOX is not actively recording
    }
  }, 80);
}

function stopCapture() {
  if (frameTimer) {
    clearInterval(frameTimer);
    frameTimer = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close().catch(() => {});
    audioContext = null;
  }
  activeMeta = null;
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== "offscreen") return;
  if (msg.type === "START_OFFSCREEN_CAPTURE") {
    startCapture(msg);
  } else if (msg.type === "STOP_OFFSCREEN_CAPTURE") {
    stopCapture();
  }
});
