const statusBox = document.getElementById("statusBox");
const btnLock = document.getElementById("btnLock");
const btnUnlock = document.getElementById("btnUnlock");

function updateStatus(res) {
  if (res && res.lockedTabId) {
    statusBox.textContent = `Locked to Tab #${res.lockedTabId} (${res.lockedMeetingSlug || "Google Meet"})`;
    statusBox.style.borderColor = "#22c55e";
  } else {
    statusBox.textContent = "No tab locked (VOX Window Title Guard active)";
    statusBox.style.borderColor = "#334155";
  }
}

chrome.runtime.sendMessage({ type: "POPUP_GET_STATUS" }, updateStatus);

btnLock.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "POPUP_START_CAPTURE" }, (res) => {
    if (res && res.ok) {
      updateStatus({ lockedTabId: res.tabId, lockedMeetingSlug: res.meetingSlug });
    } else if (res && res.error) {
      statusBox.textContent = res.error;
      statusBox.style.borderColor = "#ef4444";
    }
  });
});

btnUnlock.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "POPUP_STOP_CAPTURE" }, () => {
    updateStatus(null);
  });
});
