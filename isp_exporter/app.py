import os
import time
import requests
from prometheus_client import start_http_server, Gauge

# configuration from environment
UNIFI_KEY = os.getenv("UNIFI_API_KEY")
# controller / API base URL (you can override to hit the cloud endpoint)
CONTROLLER = os.getenv("UNIFI_CONTROLLER")
# some versions of the cloud API sit under /ea/isp-metrics/…; if
# you set UNIFI_API_URL it will be used verbatim, otherwise we fall back
# to the controller's local getispmetrics path.
UNIFI_API_URL = os.getenv("UNIFI_API_URL")
INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
VERIFY_SSL = os.getenv("VERIFY_SSL", "false").lower() in ("true", "1", "yes")
# optional controller credentials for health endpoint
UNIFI_USER = os.getenv("UNIFI_USER")
UNIFI_PASS = os.getenv("UNIFI_PASS")

# define metrics
g_download = Gauge('unifi_isp_download_bytes', 'Current ISP download throughput in bytes per second')
g_upload = Gauge('unifi_isp_upload_bytes', 'Current ISP upload throughput in bytes per second')
g_latency = Gauge('unifi_isp_latency_ms', 'ISP latency in milliseconds')
g_loss = Gauge('unifi_isp_packet_loss_percent', 'ISP packet loss percentage')

# optional list of additional endpoints to probe; comma-separated URLs
EXTRA_ENDPOINTS = [e.strip() for e in os.getenv("EXTRA_ENDPOINTS", "").split(",") if e.strip()]

# gauge for extra endpoint latency (seconds)
g_endpoint_latency = Gauge('unifi_endpoint_latency_seconds', 'Latency to custom endpoints', ['endpoint'])

# additional metrics pulled from the controller health endpoint
# values come from `/proxy/network/api/s/default/stat/health`
g_wan_status = Gauge('unifi_wan_status', 'WAN status (1=ok,0=other)')
g_wan_tx = Gauge('unifi_wan_tx_bytes_rate', 'WAN transmit bytes per second')
g_wan_rx = Gauge('unifi_wan_rx_bytes_rate', 'WAN receive bytes per second')
g_wan_availability = Gauge('unifi_wan_availability_percent', 'WAN availability percent')
g_wan_latency_avg = Gauge('unifi_wan_latency_ms', 'WAN latency average in milliseconds')
# monitors provide a list of targets; label with target/type
# each scrape we clear and reprovision to avoid stale label values
g_monitor_latency = Gauge('unifi_wan_monitor_latency_ms', 'WAN monitor latency', ['target','type'])
g_monitor_availability = Gauge('unifi_wan_monitor_availability_percent', 'WAN monitor availability', ['target','type'])

# state used to compute throughput rates between polls
_last_download = None
_last_upload = None
_last_poll = None


def fetch_metrics():
    print("fetch_metrics called", flush=True)
    # controller URL is only required if we're not using a full API URL
    if not UNIFI_KEY or (not CONTROLLER and not UNIFI_API_URL):
        print("UNIFI_API_KEY and UNIFI_CONTROLLER (or UNIFI_API_URL) must be set in environment", flush=True)
        return
    if not CONTROLLER and UNIFI_API_URL:
        # not fatal, but log what we're doing
        print("no CONTROLLER set, using UNIFI_API_URL only", flush=True)
    # some APIs (cloud) expect X-API-KEY instead of Unifi-Key
    if UNIFI_API_URL and 'api.ui.com' in UNIFI_API_URL:
        headers = {'X-API-KEY': UNIFI_KEY}
    else:
        headers = {'Unifi-Key': UNIFI_KEY}
    # choose where to pull ISP metrics from.  if a controller URL is
    # configured we attempt to hit the local site-manager path first; the
    # controller is the only source that mirrors the live rx/tx rates you can
    # fetch with the `stat/device` curl.  the cloud API (UNIFI_API_URL) is only
    # used as a fallback when the controller request fails or when no
    # controller URL is provided.
    # helper to fetch JSON from a URL, optionally using a pre-authenticated
    # session.  if the response is HTML, treat it like a failure so we can
    # fall back to the cloud API instead of silently returning the login page.
    def _request(url, session=None):
        print(f"about to request {url}", flush=True)
        if session:
            r = session.get(url, verify=VERIFY_SSL, timeout=10)
        else:
            r = requests.get(url, headers=headers, verify=VERIFY_SSL, timeout=10)
        print(f"request completed, status {r.status_code}", flush=True)
        r.raise_for_status()
        text = r.text
        if text.lstrip().startswith("<"):
            # got HTML, probably a login redirect
            print(f"received HTML from {url}, treating as failure", flush=True)
            return {}
        try:
            return r.json()
        except ValueError:
            print("non-json response:", text, flush=True)
            return {}

    try:
        data = {}
        attempted = []
        url = None
        controller_session = None
        if CONTROLLER:
            # obtain a session that can authenticate to the controller for both
            # health and ISP metrics.
            controller_session = _get_controller_session(headers)
            # local controller path
            local_url = f"{CONTROLLER.rstrip('/')}/site-manager/v1.0.0/getispmetrics"
            attempted.append(local_url)
            try:
                data = _request(local_url, session=controller_session)
                url = local_url
            except Exception as e:
                print(f"local controller request failed: {e}", flush=True)
        if not data and UNIFI_API_URL:
            # either no controller configured or local request failed
            attempted.append(UNIFI_API_URL)
            try:
                data = _request(UNIFI_API_URL)
                url = UNIFI_API_URL
            except Exception as e:
                print(f"cloud API request failed: {e}", flush=True)
        if not data:
            print(f"unable to retrieve ISP metrics from {attempted}", flush=True)
        # concise debug output - avoid huge dumps
        print(f"polled URL={url} status={(resp.status_code if 'resp' in locals() else 'n/a')} type={type(data)}", flush=True)
        if isinstance(data, dict):
            if 'data' in data:
                print("  contains data list of length", len(data['data']), flush=True)
            if 'stats' in data:
                print("  contains stats keys", list(data['stats'].keys()), flush=True)
        elif isinstance(data, list):
            print("  top-level list length", len(data), flush=True)

        # the cloud API returns {"data":[{...}]}; the controller call returns
        # {"stats":{...}}. handle both formats.
        wan = None
        if isinstance(data, dict):
            if 'stats' in data:
                wan = data['stats']
            elif 'data' in data and isinstance(data['data'], list):
                # pick latest element
                entry = data['data'][-1]
                periods = entry.get('periods', [])
                if periods:
                    wan = periods[-1].get('data', {}).get('wan', {})
        # wan may contain keys like download_kbps, avgLatency, packetLoss
        if wan is None:
            wan = {}

        # convert and set metrics
        # the API can return either a current rate in kilobits/sec or a
        # cumulative byte counter depending on the controller version.  the
        # curl example that the user showed earlier pulls the live rate from
        # `/stat/device` which is similar to the kpbs value here.  when we
        # see a "_kbps" key we treat it as an instantaneous rate and bypass
        # the delta logic; otherwise we assume the value is a counter and
        # compare against the last sample.
        now = time.time()
        def _compute_rate(bytes_key, kbps_key, gauge, last_attr_name):
            nonlocal wan, now
            raw_kbps = wan.get(kbps_key)
            raw_bytes = wan.get(bytes_key)
            rate = 0
            if raw_kbps is not None:
                # convert kilobits/sec to bytes/sec
                rate = raw_kbps * 125
                print(f"interpreting {kbps_key}={raw_kbps}kbps -> {rate}B/s", flush=True)
            elif raw_bytes is not None:
                # compute difference from previous poll
                val = raw_bytes
                prev = globals().get(last_attr_name)
                if prev is not None and _last_poll is not None:
                    delta = val - prev
                    interval = now - _last_poll
                    rate = delta / interval if interval > 0 else 0
                else:
                    rate = 0
                globals()[last_attr_name] = val
                print(f"interpreting {bytes_key}={val} -> rate {rate}B/s", flush=True)
            else:
                rate = 0
            gauge.set(rate)

        _compute_rate('download_bytes', 'download_kbps', g_download, '_last_download')
        _compute_rate('upload_bytes', 'upload_kbps', g_upload, '_last_upload')

        _last_poll = now

        latency = wan.get('latency_ms') or wan.get('avgLatency')
        g_latency.set(latency or 0)

        loss = wan.get('packet_loss_percent') or wan.get('packetLoss')
        g_loss.set(loss or 0)

        # log what rates/values we actually wrote (download/upload are rates)
        print(f"metrics set download_rate={g_download._value.get()} upload_rate={g_upload._value.get()} latency={latency} loss={loss}", flush=True)
        # probe any extra endpoints and record latency
        if EXTRA_ENDPOINTS:
            for ep in EXTRA_ENDPOINTS:
                try:
                    start = time.time()
                    resp = requests.get(ep, timeout=10, verify=VERIFY_SSL)
                    latency_val = time.time() - start
                    g_endpoint_latency.labels(endpoint=ep).set(latency_val)
                    print(f"extra endpoint {ep} latency={latency_val:.3f}s status={resp.status_code}", flush=True)
                except Exception as e:
                    print(f"error probing endpoint {ep}: {e}", flush=True)
                    # set NaN so graph shows a hole
                    g_endpoint_latency.labels(endpoint=ep).set(float('nan'))
    except Exception as e:
        print(f"error fetching ISP metrics: {e}", flush=True)

    # regardless of whether we're hitting the cloud API or the controller,
    # attempt to gather the richer /stat/health information from the controller
    # if we have any controller URL configured.  This uses the same session
    # logic as the ISP call (API key or username/password) and may still fail
    # when only the cloud API is reachable, but the error is harmless.
    #
    # previous versions skipped this step if UNIFI_API_URL was set, which
    # meant the exporter never queried the health endpoint when running
    # against the cloud API.  Always try the health call when a controller
    # URL is provided; if the controller is unreachable the error will be
    # caught and logged below.
    if CONTROLLER:
        print("CONTROLLER configured, attempting health fetch", flush=True)
        try:
            session = _get_controller_session(headers)
            _fetch_health(session)
            # new: if we have controller access attempt to read per-device WAN
            # counters.  this replicates the curl command the user ran earlier
            # and gives access to the instantaneous rx/tx rates ("-r"
            # fields).  when available we prefer these values because they
            # match the live data shown in the controller UI.
            _fetch_device(session)
        except Exception as e:
            print(f"error fetching health/ device metrics: {e}", flush=True)
    else:
        print("no CONTROLLER configured, skipping health metrics", flush=True)




def _get_controller_session(default_headers=None):
    """Return a requests.Session configured to talk to the local controller.
    If an API key is available we simply copy it into the headers; otherwise
    attempt a login using UNIFI_USER/UNIFI_PASS.  The health endpoint only
    works against a controller, not the cloud API.
    """
    s = requests.Session()
    # always send the same header as used for the ISP call if we have one
    if default_headers:
        s.headers.update(default_headers)

    # if credentials are provided, always perform an explicit login first;
    # the site-manager API requires a cookie, and the API key alone often
    # results in an HTML login page (as seen in the logs).  fall back to the
    # key only if login isn't possible.
    if UNIFI_USER and UNIFI_PASS and CONTROLLER:
        login_urls = [
            f"{CONTROLLER.rstrip('/')}/api/auth/login",
            f"{CONTROLLER.rstrip('/')}/api/login",
        ]
        last_error = None
        for login_url in login_urls:
            try:
                print(f"logging in to controller at {login_url}", flush=True)
                resp = s.post(login_url,
                              json={"username": UNIFI_USER, "password": UNIFI_PASS},
                              verify=VERIFY_SSL, timeout=10)
                resp.raise_for_status()
                # keep whatever headers we already have (API key etc.)
                return s
            except Exception as e:
                last_error = e
                print(f"controller login failed at {login_url}: {e}", flush=True)
        if last_error:
            raise last_error

    if UNIFI_KEY:
        # the controller appears to accept the same header for health
        s.headers.update({'Unifi-Key': UNIFI_KEY})
        return s

    # if we reach here we don't have any credentials that can talk to the
    # controller; the calling code should handle that gracefully.
    print("no controller credentials available, skipping health fetch", flush=True)
    return s


def _fetch_health(session):
    """Query the controller health endpoint and update extra metrics."""
    url = f"{CONTROLLER.rstrip('/')}/proxy/network/api/s/default/stat/health"
    print(f"about to request health {url}", flush=True)
    resp = session.get(url, verify=VERIFY_SSL, timeout=10)
    print(f"health request completed, status {resp.status_code}", flush=True)
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        print("non-json health response", resp.text, flush=True)
        return

    # UniFi OS returns a top-level object with a `data` array; the WAN values
    # live inside the entry where subsystem == "wan".
    wan = None
    for entry in data.get('data', []):
        if entry.get('subsystem') == 'wan':
            wan = entry
            break

    if not wan:
        print("health response did not include a WAN subsystem", flush=True)
        g_wan_status.set(0)
        g_wan_tx.set(0)
        g_wan_rx.set(0)
        g_wan_availability.set(0)
        g_wan_latency_avg.set(0)
        return

    # status and simple fields from the WAN subsystem block
    status = 1 if wan.get('status') == 'ok' else 0
    g_wan_status.set(status)
    g_wan_tx.set(wan.get('tx_bytes-r') or wan.get('tx_bytes_r') or 0)
    g_wan_rx.set(wan.get('rx_bytes-r') or wan.get('rx_bytes_r') or 0)

    wanstats = wan.get('uptime_stats', {}).get('WAN', {})
    availability = wanstats.get('availability') or 0
    latency_average = wanstats.get('latency_average') or 0
    g_wan_availability.set(availability)
    g_wan_latency_avg.set(latency_average)

    # the local site-manager ISP metrics often omit latency entirely, which
    # leaves the ISP Latency panel pinned at 0 even though the controller health
    # endpoint has a valid WAN latency.  use the WAN health value as a fallback
    # for the ISP latency gauge whenever it is missing or zero.
    if latency_average and g_latency._value.get() == 0:
        g_latency.set(latency_average)
    print(
        f"health WAN status={status} tx={g_wan_tx._value.get()} rx={g_wan_rx._value.get()} availability={g_wan_availability._value.get()} latency={g_wan_latency_avg._value.get()} isp_latency={g_latency._value.get()}",
        flush=True,
    )

    # clear previous monitor labels to avoid stale data
    try:
        g_monitor_latency.clear()
        g_monitor_availability.clear()
    except NameError:
        pass

    monitors = wanstats.get('monitors', []) or wanstats.get('alerting_monitors', [])
    for m in monitors:
        tgt = m.get('target', '')
        typ = m.get('type', '')
        g_monitor_latency.labels(target=tgt, type=typ).set(m.get('latency_average') or 0)
        g_monitor_availability.labels(target=tgt, type=typ).set(m.get('availability') or 0)


def _fetch_device(session):
    """Fetch per-device WAN counters and update throughput gauges."""
    url = f"{CONTROLLER.rstrip('/')}/proxy/network/api/s/default/stat/device"
    print(f"about to request device stats {url}", flush=True)
    resp = session.get(url, verify=VERIFY_SSL, timeout=10)
    print(f"device request completed, status {resp.status_code}", flush=True)
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        print("non-json device response", resp.text, flush=True)
        return

    # response contains a list of devices; look for any with wan1/wan2
    for dev in data.get('data', []):
        wan = dev.get('wan1') or dev.get('wan2')
        if not wan:
            continue
        # the fields ending in "-r" are already a rate in bytes/sec
        rx = wan.get('rx_bytes-r') or wan.get('rx_bytes_r')
        tx = wan.get('tx_bytes-r') or wan.get('tx_bytes_r')
        if rx is not None or tx is not None:
            print(f"device metrics rx={rx} tx={tx}", flush=True)
        if rx is not None:
            g_download.set(rx)
        if tx is not None:
            g_upload.set(tx)
        # we only need the first WAN-capable device
        break


if __name__ == '__main__':
    # debug: print configuration
    print(f"ISP exporter configuration - API_KEY={'***' if UNIFI_KEY else None}, "
          f"CONTROLLER={CONTROLLER}, API_URL={UNIFI_API_URL}, INTERVAL={INTERVAL}, VERIFY_SSL={VERIFY_SSL}, "
          f"UNIFI_USER={UNIFI_USER}", flush=True)
    # start up the server to expose the metrics.
    start_http_server(9100)
    print("ISP exporter listening on :9100, polling every {}s".format(INTERVAL), flush=True)
    while True:
        fetch_metrics()
        time.sleep(INTERVAL)