// VOX Call-Only Google Meet Tab Capture — Manifest V3 Service Worker
const BRIDGE_URL = "http://127.0.0.1:49231";
let lockedTabId = null;
let lockedMeetingSlug = "";

function extractMeetSlug(text) {
  const m = (text || "").match(/\b([a-z]{3}-[a-z]{4}-[a-z]{3})\b/i);
  return m ? m[1].toLowerCase() : "";
}

async function ensureOffscreenDocument() {
  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"]
  });
  if (existingContexts.length > 0) {
    return;
  }
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Capture isolated Google Meet tab video & audio for VOX Call-Only Meeting Recording"
  });
}

async function startTabLockCapture(tab) {
  if (!tab || !tab.id) {
    return { ok: false, error: "No active tab found" };
  }
  const url = tab.url || "";
  const title = tab.title || "";
  if (!url.includes("meet.google.com") && !title.toLowerCase().includes("meet")) {
    return { ok: false, error: "Selected tab is not a Google Meet session" };
  }

  await ensureOffscreenDocument();
  const streamId = await chrome.tabCapture.getMediaStreamId({
    targetTabId: tab.id
  });

  lockedTabId = tab.id;
  lockedMeetingSlug = extractMeetSlug(url + " " + title);

  try {
    await fetch(`${BRIDGE_URL}/register_tab`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tab_id: lockedTabId,
        title: title,
        url: url,
        meeting_slug: lockedMeetingSlug
      })
    });
  } catch (err) {
    // Bridge will receive frames once VOX is running
  }

  chrome.runtime.sendMessage({
    type: "START_OFFSCREEN_CAPTURE",
    target: "offscreen",
    streamId: streamId,
    tabId: lockedTabId,
    title: title,
    url: url,
    meetingSlug: lockedMeetingSlug
  });

  await chrome.storage.local.set({
    lockedTabId: lockedTabId,
    lockedTitle: title,
    lockedMeetingSlug: lockedMeetingSlug
  });

  return {
    ok: true,
    tabId: lockedTabId,
    title: title,
    meetingSlug: lockedMeetingSlug
  };
}

async function stopTabLockCapture() {
  const prevTabId = lockedTabId;
  lockedTabId = null;
  lockedMeetingSlug = "";

  chrome.runtime.sendMessage({
    type: "STOP_OFFSCREEN_CAPTURE",
    target: "offscreen"
  });

  if (prevTabId) {
    try {
      await fetch(`${BRIDGE_URL}/unregister_tab`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tab_id: prevTabId })
      });
    } catch (err) {}
  }

  await chrome.storage.local.remove(["lockedTabId", "lockedTitle", "lockedMeetingSlug"]);
  return { ok: true };
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === lockedTabId) {
    stopTabLockCapture();
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "POPUP_START_CAPTURE") {
    chrome.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      const res = await startTabLockCapture(tabs[0]);
      sendResponse(res);
    });
    return true;
  }
  if (msg.type === "POPUP_STOP_CAPTURE") {
    stopTabLockCapture().then((res) => sendResponse(res));
    return true;
  }
  if (msg.type === "POPUP_GET_STATUS") {
    sendResponse({
      lockedTabId: lockedTabId,
      lockedMeetingSlug: lockedMeetingSlug
    });
    return true;
  }
});
