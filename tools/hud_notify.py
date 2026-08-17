"""OS-level reminders for HUD todos (works when HUD window is minimized)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading

logger = logging.getLogger(__name__)


def notify_reminder(title: str, body: str) -> None:
    """Show a Windows toast/balloon; never raise to callers."""
    title = (title or "Immortility").strip()[:80] or "Immortility"
    body = (body or "").strip()[:240] or "Reminder due"
    threading.Thread(
        target=_notify_windows,
        args=(title, body),
        name="hud-os-notify",
        daemon=True,
    ).start()


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _notify_windows(title: str, body: str) -> None:
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        logger.debug("No PowerShell — skip OS reminder toast")
        return
    title_x = _xml_escape(title)
    body_x = _xml_escape(body)
    # Prefer Win10+ toast; fall back to tray balloon (still visible when HUD minimized).
    script = f"""
$ErrorActionPreference = 'Stop'
$title = {_ps_quote(title)}
$body = {_ps_quote(body)}
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
  $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
  $xml.LoadXml(@'
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{title_x}</text>
      <text>{body_x}</text>
    </binding>
  </visual>
</toast>
'@)
  $appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
  exit 0
}} catch {{
  try {{
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $n = New-Object System.Windows.Forms.NotifyIcon
    $n.Icon = [System.Drawing.SystemIcons]::Information
    $n.Visible = $true
    $n.BalloonTipTitle = $title
    $n.BalloonTipText = $body
    $n.ShowBalloonTip(10000)
    Start-Sleep -Seconds 11
    $n.Dispose()
    exit 0
  }} catch {{
    Write-Error $_.Exception.Message
    exit 1
  }}
}}
"""
    try:
        proc = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if proc.returncode != 0:
            logger.debug(
                "OS reminder failed rc=%s stderr=%s",
                proc.returncode,
                (proc.stderr or "")[:300],
            )
    except Exception as exc:
        logger.debug("OS reminder toast error: %s", exc)
