import demistomock as demisto
from CommonServerPython import *
import re
from requests import Session
import urllib3
import functools
import json
from datetime import datetime
from requests import cookies
import pytz
from typing import Dict, Any

# disable insecure warnings
urllib3.disable_warnings()


''' GLOBAL VARIABLES'''
ACTION_TYPE_TO_VALUE = {
    'notification': 'users.username',
    'email': 'users.username',
    'syslog': 'host',
    'scan': 'scan.name',
    'report': 'report.name',
    'ticket': 'assignee.username'
}


class Client(BaseClient):
    def __init__(self, verify_ssl: bool = True, proxy: bool = False, user_name: str = "",
                 password: str = "", access_key: str = "", secret_key: str = "", url: str = ""):

        if not proxy:
            del os.environ['HTTP_PROXY']
            del os.environ['HTTPS_PROXY']
            del os.environ['http_proxy']
            del os.environ['https_proxy']

        self.url = f"{get_server_url(url)}/rest"
        self.verify_ssl = verify_ssl
        self.max_retries = 3
        self.headers: dict[str, Any] = {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        if not (user_name and password) and not (secret_key and access_key):
            return_error("Please provide either user_name and password or secret_key and access_key")
        if secret_key and access_key:
            self.headers['x-apikey'] = f"accesskey={access_key}; secretkey={secret_key}"
            BaseClient.__init__(self, base_url=self.url, headers=self.headers, verify=verify_ssl, proxy=proxy)
            self.send_request = self.send_request_new
        else:
            self.session = Session()
            integration_context = demisto.getIntegrationContext()
            self.token = integration_context.get('token')
            self.cookie = integration_context.get('cookie')
            self.user_name = user_name
            self.password = password
            self.send_request = self.send_request_old
            if not self.token or not self.cookie:
                self.login()

    def send_request_new(self, path, method='get', body=None, params=None, headers=None):
        headers = headers or self.headers
        return self._http_request(method, url_suffix=path, params=params, data=json.dumps(body))

    def send_request_old(self, path, method='get', body=None, params=None, headers=None, try_number=1):
        body = body if body is not None else {}
        params = params if params is not None else {}
        headers = headers if headers is not None else self.headers

        headers['X-SecurityCenter'] = self.token
        url = '{}/{}'.format(self.url, path)

        session_cookie = cookies.create_cookie('TNS_SESSIONID', self.cookie)
        self.session.cookies.set_cookie(session_cookie)  # type: ignore

        res = self.session.request(method, url, data=json.dumps(body), params=params, headers=headers, verify=self.verify_ssl)

        if res.status_code == 403 and try_number <= self.max_retries:
            self.login()
            headers['X-SecurityCenter'] = self.token  # The Token is being updated in the login
            return self.send_request(path, method, body, params, headers, try_number + 1)

        elif res.status_code < 200 or res.status_code >= 300:
            try:
                error = res.json()
            except Exception:
                # type: ignore
                return_error(
                    f'Error: Got status code {str(res.status_code)} with {url=} \
                    with body {res.content} with headers {str(res.headers)}')   # type: ignore

            return_error(f"Error: Got an error from TenableSC, code: {error['error_code']}, \
                        details: {error['error_msg']}")  # type: ignore
        return res.json()

    def login(self):
        login_body = {
            'username': self.user_name,
            'password': self.password
        }
        login_response = self.send_login_request(login_body)

        if 'response' not in login_response:
            return_error('Error: Could not retrieve login token')

        token = login_response['response'].get('token')
        # There might be a case where the API does not return a token because there are too many sessions with the same user
        # In that case we need to add 'releaseSession = true'
        if not token:
            login_body['releaseSession'] = 'true'
            login_response = self.send_login_request(login_body)
            if 'response' not in login_response or 'token' not in login_response['response']:
                return_error('Error: Could not retrieve login token')
            token = login_response['response']['token']

        self.token = str(token)
        demisto.setIntegrationContext({'token': token})

    def send_login_request(self, login_body):
        url = f'{self.url}/token'

        headers = self.headers
        res = self.session.request('post', url, headers=headers, data=json.dumps(login_body), verify=self.verify_ssl)

        if res.status_code < 200 or res.status_code >= 300:
            return_error(f'Error: Got status code {str(res.status_code)} with {url=} \
                        with body {res.content} with headers {str(res.headers)}')  # type: ignore

        cookie = res.cookies.get('TNS_SESSIONID', self.cookie)
        demisto.setIntegrationContext({'cookie': cookie})

        return res.json()

    def logout(self):
        self.send_request(path='token', method='delete')

    def create_scan(self, name, repo_id, policy_id, plugin_id, description, zone_id, schedule, asset_ids,
                    ips, scan_virtual_hosts, report_ids, credentials, timeout_action, max_scan_time,
                    dhcp_track, rollover_type, dependent, start_time="", repeat_rule_freq="", repeat_rule_interval="",
                    repeat_rule_by_day="", enabled=True, time_zone=""):

        scan_type = 'policy' if policy_id else 'plugin'

        body = {
            'name': name,
            'type': scan_type,
            'repository': {
                'id': repo_id
            }
        }

        if policy_id:
            body['policy'] = {
                'id': policy_id
            }

        if plugin_id:
            body['pluginID'] = plugin_id

        if description:
            body['description'] = description

        if zone_id:
            body['zone'] = {
                'id': zone_id
            }

        if dhcp_track:
            body['dhcpTracking'] = dhcp_track

        if schedule:
            body['schedule'] = {
                'type': schedule
            }

            if dependent:
                body['schedule']['dependentID'] = dependent

            if schedule == 'ical':
                timestamp_format = "%Y%m%dT%H%M%S"
                expected_format = "%Y-%m-%d:%H:%M:%S"
                try:
                    start_time = datetime.strptime(start_time, expected_format)
                    start_time = datetime.strftime(start_time, timestamp_format)
                except Exception:
                    start_time = parse_date_range(start_time, date_format=timestamp_format)[0]
                if time_zone and start_time:
                    body['schedule']['start'] = f"TZID={time_zone}:{start_time}"
                else:
                    return_error("Please make sure to provide both time_zone and start_time.")
                if all(repeat_rule_freq, repeat_rule_interval, repeat_rule_by_day):
                    body['schedule']['repeatRule'] = f"FREQ={repeat_rule_freq};INTERVAL={repeat_rule_interval};BYDAY={repeat_rule_by_day}"
                elif repeat_rule_freq and repeat_rule_interval:
                    body['schedule']['repeatRule'] = f"FREQ={repeat_rule_freq};INTERVAL={repeat_rule_interval}"
                elif any(repeat_rule_freq, repeat_rule_interval, repeat_rule_by_day):
                    return_error("Please make sure to provide repeat_rule_freq, repeat_rule_interval with or without "
                                 "repeat_rule_by_day, or don't provide any of them.")
                body['schedule']['enabled'] = enabled

        if report_ids:
            body['reports'] = [{'id': r_id, 'reportSource': 'individual'} for r_id in argToList(report_ids)]

        if asset_ids:
            if str(asset_ids).startswith('All'):
                manageable = True if asset_ids == 'AllManageable' else False
                res = self.get_assets(None)
                assets = get_elements(res['response'], manageable)
                asset_ids = list(map(lambda a: a['id'], assets))
            body['assets'] = [{'id': a_id} for a_id in argToList(asset_ids)]

        if credentials:
            body['credentials'] = [{'id': c_id} for c_id in argToList(credentials)]

        if timeout_action:
            body['timeoutAction'] = timeout_action

        if scan_virtual_hosts:
            body['scanningVirtualHosts'] = scan_virtual_hosts

        if rollover_type:
            body['rolloverType'] = rollover_type

        if ips:
            body['ipList'] = ips

        if max_scan_time:
            body['maxScanTime'] = max_scan_time * 3600

        return self.send_request(path='scan', method='post', body=body)

    def get_scan_results(self, scan_results_id):
        path = 'scanResult/' + scan_results_id

        return self.send_request(path)

    def launch_scan(self, scan_id, scan_target):
        path = 'scan/' + scan_id + '/launch'
        body = None
        if scan_target:
            body = {
                'diagnosticTarget': scan_target['address'],
                'diagnosticPassword': scan_target['password']
            }

        return self.send_request(path, 'post', body=body)

    def get_query(self, query_id):
        path = 'query/' + query_id

        return self.send_request(path)

    def get_all_scan_results(self):
        params = {
            'fields': 'name,description,details,status,scannedIPs,startTime,scanDuration,importStart,'
            'finishTime,completedChecks,owner,ownerGroup,repository'
        }
        return self.send_request(path='scanResult', params=params)

    def get_alerts(self, fields=None, alert_id=None):
        path = 'alert'
        params = {}  # type: Dict[str, Any]

        if alert_id:
            path += '/' + alert_id

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path, params=params)

    def get_system_licensing(self):
        return self.send_request(path='status')

    def get_scans(self, fields):
        params = None

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path='scan', params=params)

    def get_policies(self, fields):
        params = None

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path='policy', params=params)

    def get_repositories(self):
        return self.send_request(path='repository')

    def get_assets(self, fields):
        params = None

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path='asset', params=params)

    def get_credentials(self, fields):
        params = None

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path='credential', params=params)

    def get_asset(self, asset_id):
        params = {
            'fields': 'id,name,description,status,createdTime,modifiedTime,viewableIPs,ownerGroup,tags,owner'
        }

        return self.send_request(path=f'asset/{asset_id}', params=params)

    def create_asset(self, name, description, owner_id, tags, ips):
        body = {
            'name': name,
            'definedIPs': ips,
            'type': 'static'
        }

        if description:
            body['description'] = description

        if owner_id:
            body['ownerID'] = owner_id

        if tags:
            body['tags'] = tags

        return self.send_request(path='asset', method='post', body=body)

    def delete_asset(self, asset_id):
        return self.send_request(path=f'asset/{asset_id}', method='delete')

    def get_report_definitions(self, fields):
        params = None

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path='reportDefinition', params=params)

    def get_zones(self):
        return self.send_request(path='zone')

    def get_scan_report(self, scan_results_id):
        path = 'scanResult/' + scan_results_id

        params = {
            'fields': 'name,description,details,status,scannedIPs,progress,startTime,scanDuration,importStart,'
                      'finishTime,completedChecks,owner,ownerGroup,repository,policy'
        }

        return self.send_request(path, params=params)

    def create_query(self, scan_id, tool, query_filters=None):
        path = 'query'

        body = {
            'name': 'scan ' + scan_id + ' query',
            'type': 'vuln',
            'tool': tool,
            'scanID': scan_id
        }

        if query_filters:
            body['filters'] = query_filters

        return self.send_request(path, method='post', body=body)

    def delete_query(self, query_id):
        if not query_id:
            return_error('query id returned None')
        path = 'query/' + str(query_id)
        self.send_request(path, method='delete')

    def get_analysis(self, query, scan_results_id):
        path = 'analysis'

        # This function can receive 'query' argument either as a dict (as in get_vulnerability_command),
        # or as an ID of an existing query (as in get_vulnerabilities).
        # Here we form the query field in the request body as a dict, as required.
        if not isinstance(query, dict):
            query = {'id': query}

        body = {
            'type': 'vuln',
            'query': query,
            'sourceType': 'individual',
            'scanID': scan_results_id,
            'view': 'all'
        }

        return self.send_request(path, method='post', body=body)

    def list_plugins(self, name, plugin_type, cve):
        params = {
            'fields': 'id,type,name,description,family'
        }

        if cve:
            params['filterField'] = 'xrefs:CVE'
            params['op'] = 'eq'
            params['value'] = cve

        if plugin_type:
            params['type'] = plugin_type

        return self.send_request(path='plugin', params=params)

    def get_system_diagnostics(self):
        return self.send_request(path='system/diagnostics')

    def get_system(self):
        return self.send_request(path='system')

    def change_scan_status(self, scan_results_id, status):
        path = 'scanResult/' + scan_results_id + '/' + status

        return self.send_request(path, method='post')

    def get_vulnerability(self, vuln_id):
        path = 'plugin/' + vuln_id

        params = {
            'fields': 'name,description,family,type,cpe,riskFactor,solution,synopsis,exploitEase,exploitAvailable,'
                      'cvssVector,baseScore,pluginPubDate,pluginModDate,vulnPubDate,temporalScore,xrefs,checkType'
        }

        return self.send_request(path, params=params)

    def delete_scan(self, scan_id):
        return self.send_request(path=f'scan/{scan_id}', method='delete')

    def get_device(self, uuid, ip, dns_name, repo):
        path = 'repository/' + repo + '/' if repo else ''
        path += 'deviceInfo'
        params = {
            'fields': 'ip,uuid,macAddress,netbiosName,dnsName,os,osCPE,lastScan,repository,total,severityLow,'
                      'severityMedium,severityHigh,severityCritical'
        }
        if uuid:
            params['uuid'] = uuid
        else:
            params['ip'] = ip
            if dns_name:
                params['dnsName'] = dns_name

        return self.send_request(path, params=params)

    def get_users(self, fields, user_id):
        path = 'user'

        if user_id:
            path += '/' + user_id

        params = None

        if fields:
            params = {
                'fields': fields
            }

        return self.send_request(path, params=params)


''' HELPER FUNCTIONS '''


def get_server_url(url):
    url = re.sub('/[\/]+$/', '', url)
    url = re.sub('\/$', '', url)
    return url


def return_message(msg):
    demisto.results(msg)
    sys.exit(0)


''' FUNCTIONS '''


def list_scans_command(client: Client, args: Dict[str, Any]):
    res = client.get_scans('id,name,description,policy,ownerGroup,owner')
    manageable = args.get('manageable', 'false').lower()

    if not res or 'response' not in res or not res['response']:
        return_message('No scans found')

    scans_dicts = get_elements(res['response'], manageable)

    if len(scans_dicts) == 0:
        return_message('No scans found')

    headers = ['ID', 'Name', 'Description', 'Policy', 'Group', 'Owner']

    mapped_scans = [{
        'Name': s['name'],
        'ID': s['id'],
        'Description': s['description'],
        'Policy': s['policy'].get('name'),
        'Group': s['ownerGroup'].get('name'),
        'Owner': s['owner'].get('username')
    } for s in scans_dicts]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Scans', mapped_scans, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Scan(val.ID===obj.ID)': createContext(mapped_scans, removeNull=True)
        }
    })


def list_policies_command(client: Client, args: Dict[str, Any]):
    res = client.get_policies('id,name,description,tags,modifiedTime,owner,ownerGroup,policyTemplate')

    manageable = args.get('manageable', 'false').lower()

    if not res or 'response' not in res or not res['response']:
        return_message('No policies found')

    policies = get_elements(res['response'], manageable)

    if len(policies) == 0:
        return_message('No policies found')

    headers = ['ID', 'Name', 'Description', 'Tag', 'Type', 'Group', 'Owner', 'LastModified']

    mapped_policies = [{
        'ID': p['id'],
        'Name': p['name'],
        'Description': p['description'],
        'Tag': p['tags'],
        'Type': p.get('policyTemplate', {}).get('name'),
        'Group': p.get('ownerGroup', {}).get('name'),
        'Owner': p.get('owner', {}).get('username'),
        'LastModified': timestamp_to_utc(p['modifiedTime'])
    } for p in policies]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Scan Policies', mapped_policies, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.ScanPolicy(val.ID===obj.ID)': createContext(mapped_policies, removeNull=True)
        }
    })


def list_repositories_command(client: Client, args: Dict[str, Any]):
    res = client.get_repositories()

    if not res or 'response' not in res or not res['response']:
        return_message('No repositories found')

    repositories = res['response']

    if len(repositories) == 0:
        return_message('No repositories found')

    headers = [
        'ID',
        'Name',
        'Description'
    ]

    mapped_repositories = [{'ID': r['id'], 'Name': r['name'], 'Description': r['description']} for r in repositories]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Scan Repositories', mapped_repositories, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.ScanRepository(val.ID===obj.ID)': createContext(mapped_repositories, removeNull=True)
        }
    })


def list_credentials_command(client: Client, args: Dict[str, Any]):
    res = client.get_credentials('id,name,description,type,ownerGroup,owner,tags,modifiedTime')

    manageable = args.get('manageable', 'false').lower()

    if not res or 'response' not in res or not res['response']:
        return_message('No credentials found')

    credentials = get_elements(res['response'], manageable)

    if len(credentials) == 0:
        return_message('No credentials found')

    headers = ['ID', 'Name', 'Description', 'Type', 'Tag', 'Group', 'Owner', 'LastModified']

    mapped_credentials = [{
        'ID': c['id'],
        'Name': c['name'],
        'Description': c['description'],
        'Type': c['type'],
        'Tag': c['tags'],
        'Group': c.get('ownerGroup', {}).get('name'),
        'Owner': c.get('owner', {}).get('name'),
        'LastModified': timestamp_to_utc(c['modifiedTime'])
    } for c in credentials]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Credentials', mapped_credentials, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Credential(val.ID===obj.ID)': createContext(mapped_credentials, removeNull=True)
        }
    })


def list_assets_command(client: Client, args: Dict[str, Any]):
    res = client.get_assets('id,name,description,ipCount,type,tags,modifiedTime,groups,owner')

    manageable = args.get('manageable', 'false').lower()

    if not res or 'response' not in res or not res['response']:
        return_message('No assets found')

    assets = get_elements(res['response'], manageable)

    if len(assets) == 0:
        return_message('No assets found')

    headers = ['ID', 'Name', 'Tag', 'Owner', 'Group', 'Type', 'HostCount', 'LastModified']

    mapped_assets = [{
        'ID': a['id'],
        'Name': a['name'],
        'Tag': a['tags'],
        'Owner': a.get('owner', {}).get('username'),
        'Type': a['type'],
        'Group': a.get('ownerGroup', {}).get('name'),
        'HostCount': a['ipCount'],
        'LastModified': timestamp_to_utc(a['modifiedTime'])
    } for a in assets]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Assets', mapped_assets, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Asset(val.ID===obj.ID)': createContext(mapped_assets, removeNull=True)
        }
    })


def get_asset_command(client: Client, args: Dict[str, Any]):
    asset_id = args.get('asset_id')

    res = client.get_asset(asset_id)

    if not res or 'response' not in res:
        return_message('Asset not found')

    asset = res['response']

    ips = []  # type: List[str]
    ip_lists = [v['ipList'] for v in asset['viewableIPs']]

    for ip_list in ip_lists:
        # Extract IPs
        ips += re.findall('[0-9]+(?:\.[0-9]+){3}', ip_list)

    headers = ['ID', 'Name', 'Description', 'Tag', 'Created', 'Modified', 'Owner', 'Group', 'IPs']

    mapped_asset = {
        'ID': asset['id'],
        'Name': asset['name'],
        'Description': asset['description'],
        'Tag': asset['tags'],
        'Created': timestamp_to_utc(asset['createdTime']),
        'Modified': timestamp_to_utc(asset['modifiedTime']),
        'Owner': asset.get('owner', {}).get('username'),
        'Group': asset.get('ownerGroup', {}).get('name'),
        'IPs': ips
    }

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Asset', mapped_asset, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Asset(val.ID===obj.ID)': createContext(mapped_asset, removeNull=True)
        }
    })


def create_asset_command(client: Client, args: Dict[str, Any]):
    name = args.get('name')
    description = args.get('description')
    owner_id = args.get('owner_id')
    tags = args.get('tags')
    ips = args.get('ip_list')

    res = client.create_asset(name, description, owner_id, tags, ips)

    if not res or 'response' not in res:
        return_error('Error: Could not retrieve the asset')

    asset = res['response']

    mapped_asset = {
        'ID': asset['id'],
        'Name': asset['name'],
        'OwnerName': asset['owner'].get('username'),
        'Tags': asset['tags'],
    }

    headers = [
        'ID',
        'Name',
        'OwnerName',
        'Tags'
    ]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Asset created successfully', mapped_asset, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Asset(val.ID===obj.ID)': createContext(mapped_asset, removeNull=True)
        }
    })


def delete_asset_command(client: Client, args: Dict[str, Any]):
    asset_id = args.get('asset_id')

    res = client.delete_asset(asset_id)

    if not res:
        return_error('Error: Could not delete the asset')

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['text'],
        'HumanReadable': 'Asset successfully deleted'
    })


def list_report_definitions_command(client: Client, args: Dict[str, Any]):
    res = client.get_report_definitions('id,name,description,modifiedTime,type,ownerGroup,owner')

    manageable = args.get('manageable', 'false').lower()

    if not res or 'response' not in res or not res['response']:
        return_message('No report definitions found')

    reports = get_elements(res['response'], manageable)
    # Remove duplicates, take latest
    reports = [functools.reduce(lambda x, y: x if int(x['modifiedTime']) > int(y['modifiedTime']) else y,
                                filter(lambda e: e['name'] == n, reports)) for n in {r['name'] for r in reports}]

    if len(reports) == 0:
        return_message('No report definitions found')

    headers = ['ID', 'Name', 'Description', 'Type', 'Group', 'Owner']

    mapped_reports = [{
        'ID': r['id'],
        'Name': r['name'],
        'Description': r['description'],
        'Type': r['type'],
        'Group': r.get('ownerGroup', {}).get('name'),
        'Owner': r.get('owner', {}).get('username')
    } for r in reports]

    hr = tableToMarkdown('Tenable.sc Report Definitions', mapped_reports, headers, removeNull=True)
    for r in mapped_reports:
        del r['Description']

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': hr,
        'EntryContext': {
            'TenableSC.ReportDefinition(val.ID===obj.ID)': createContext(mapped_reports, removeNull=True)
        }
    })


def list_zones_command(client: Client, args: Dict[str, Any]):
    res = client.get_zones()

    if not res or 'response' not in res:
        return_message('No zones found')

    zones = res['response']
    if len(zones) == 0:
        zones = [{
            'id': 0,
            'name': 'All Zones',
            'description': '',
            'ipList': '',
            'activeScanners': ''
        }]

    headers = ['ID', 'Name', 'Description', 'IPList', 'ActiveScanners']

    mapped_zones = [{
        'ID': z['id'],
        'Name': z['name'],
        'Description': z['description'],
        'IPList': z['ipList'],
        'ActiveScanners': z['activeScanners']
    } for z in zones]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Scan Zones', mapped_zones, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.ScanZone(val.ID===obj.ID)': createContext(mapped_zones, removeNull=True)
        }
    })


def get_elements(elements, manageable):
    if manageable == 'false':
        return elements.get('usable')

    return elements.get('manageable')


def create_scan_command(client: Client, args: Dict[str, Any]):
    name = args.get('name')
    repo_id = args.get('repository_id')
    policy_id = args.get('policy_id')
    plugin_id = args.get('plugin_id')
    description = args.get('description')
    zone_id = args.get('zone_id')
    schedule = args.get('schedule')
    asset_ids = args.get('asset_ids')
    ips = args.get('ip_list')
    scan_virtual_hosts = args.get('scan_virtual_hosts')
    report_ids = args.get('report_ids')
    credentials = args.get('credentials')
    timeout_action = args.get('timeout_action')
    max_scan_time = args.get('max_scan_time')
    dhcp_track = args.get('dhcp_tracking')
    rollover_type = args.get('rollover_type')
    dependent = args.get('dependent_id')
    time_zone = args.get("time_zone")
    start_time = args.get("start_time")
    repeat_rule_freq = args.get("repeat_rule_freq", "")
    repeat_rule_interval = int(args.get("repeat_rule_interval", 0))
    repeat_rule_by_day = argToList(args.get("repeat_rule_by_day"), "")
    enabled = argToBoolean(args.get("enabled", True))

    if time_zone and time_zone not in pytz.all_timezones:
        return_error("Invalid time zone ID. Please choose one of the following: "
                     "https://docs.oracle.com/middleware/1221/wcs/tag-ref/MISC/TimeZones.html")
    if not asset_ids and not ips:
        return_error('Error: Assets and/or IPs must be provided')

    if schedule == 'dependent' and not dependent:
        return_error('Error: Dependent schedule must include a dependent scan ID')

    res = client.create_scan(name, repo_id, policy_id, plugin_id, description, zone_id, schedule, asset_ids,
                             ips, scan_virtual_hosts, report_ids, credentials, timeout_action, max_scan_time,
                             dhcp_track, rollover_type, dependent, start_time, repeat_rule_freq, repeat_rule_interval,
                             repeat_rule_by_day, enabled, time_zone)

    if not res or 'response' not in res:
        return_error('Error: Could not retrieve the scan')

    scan = res['response']

    headers = [
        'ID',
        'CreatorID',
        'Name',
        'Type',
        'CreationTime',
        'OwnerName',
        'Reports'
    ]

    mapped_scan = {
        'ID': scan['id'],
        'CreatorID': scan['creator'].get('id'),
        'Name': scan['name'],
        'Type': scan['type'],
        'CreationTime': timestamp_to_utc(scan['createdTime']),
        'OwnerName': scan['owner'].get('name'),
        'Reports': demisto.dt(scan['reports'], 'id')
    }

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Scan created successfully', mapped_scan, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Scan(val.ID===obj.ID)': createContext(mapped_scan, removeNull=True)
        }
    })


def launch_scan_command(client: Client, args: Dict[str, Any]):
    scan_id = args.get('scan_id')
    target_address = args.get('diagnostic_target')
    target_password = args.get('diagnostic_password')

    if (target_address and not target_password) or (target_password and not target_address):
        return_error('Error: If a target is provided, both IP/Hostname and the password must be provided')

    res = client.launch_scan(scan_id, {'address': target_address, 'password': target_password})

    if not res or 'response' not in res or not res['response'] or 'scanResult' not in res['response']:
        return_error('Error: Could not retrieve the scan')

    scan_result = res['response']['scanResult']

    headers = [
        'Name',
        'ID',
        'OwnerID',
        'JobID',
        'Status'
    ]

    mapped_scan = {
        'Name': scan_result['name'],
        'ID': scan_result['id'],
        'OwnerID': scan_result['ownerID'],
        'JobID': scan_result['jobID'],
        'Status': scan_result['status']
    }

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Scan', mapped_scan, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.ScanResults(val.ID===obj.ID)': createContext(mapped_scan, removeNull=True)
        }
    })


def get_scan_status_command(client: Client, args: Dict[str, Any]):
    scan_results_ids = argToList(args.get('scan_results_id'))

    scans_results = []
    for scan_results_id in scan_results_ids:
        res = client.get_scan_results(scan_results_id)
        if not res or 'response' not in res or not res['response']:
            return_message('Scan results not found')

        scans_results.append(res['response'])

    headers = ['ID', 'Name', 'Status', 'Description']

    mapped_scans_results = [{
        'ID': scan_result['id'],
        'Name': scan_result['name'],
        'Status': scan_result['status'],
        'Description': scan_result['description']
    } for scan_result in scans_results]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Scan Status', mapped_scans_results, headers, removeNull=True),
        'EntryContext': {
            'TenableSC.ScanResults(val.ID===obj.ID)': createContext(mapped_scans_results, removeNull=True)
        }
    })


def get_scan_report_command(client: Client, args: Dict[str, Any]):
    scan_results_id = args.get('scan_results_id')
    vulnerabilities_to_get = argToList(args.get('vulnerability_severity', []))

    res = client.get_scan_report(scan_results_id)

    if not res or 'response' not in res or not res['response']:
        return_message('Scan results not found')

    scan_results = res['response']

    headers = ['ID', 'Name', 'Description', 'Policy', 'Group', 'Owner', 'ScannedIPs',
               'StartTime', 'EndTime', 'Duration', 'Checks', 'ImportTime', 'RepositoryName', 'Status']
    vuln_headers = ['ID', 'Name', 'Family', 'Severity', 'Total']

    mapped_results = {
        'ID': scan_results['id'],
        'Name': scan_results['name'],
        'Status': scan_results['status'],
        'Description': scan_results['description'],
        'Policy': scan_results['details'],
        'Group': scan_results.get('ownerGroup', {}).get('name'),
        'Checks': scan_results.get('completedChecks'),
        'StartTime': timestamp_to_utc(scan_results['startTime']),
        'EndTime': timestamp_to_utc(scan_results['finishTime']),
        'Duration': scan_duration_to_demisto_format(scan_results['scanDuration']),
        'ImportTime': timestamp_to_utc(scan_results['importStart']),
        'ScannedIPs': scan_results['scannedIPs'],
        'Owner': scan_results['owner'].get('username'),
        'RepositoryName': scan_results['repository'].get('name')
    }

    hr = tableToMarkdown('Tenable.sc Scan ' + mapped_results['ID'] + ' Report',
                         mapped_results, headers, removeNull=True)

    if len(vulnerabilities_to_get) > 0:
        vulns = get_vulnerabilities(client, scan_results_id)

        if isinstance(vulns, list):
            vulnerabilities = list(filter(lambda v: v['Severity'] in vulnerabilities_to_get, vulns))
            if vulnerabilities and len(vulnerabilities) > 0:
                hr += tableToMarkdown('Vulnerabilities', vulnerabilities, vuln_headers, removeNull=True)
                mapped_results['Vulnerability'] = vulnerabilities

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': hr,
        'EntryContext': {
            'TenableSC.ScanResults(val.ID===obj.ID)': createContext(mapped_results, removeNull=True)
        }
    })


def list_plugins_command(client: Client, args: Dict[str, Any]):
    name = args.get('name'),
    cve = args.get('cve'),
    plugin_type = args.get('type')

    res = client.list_plugins(name, plugin_type, cve)

    if not res or 'response' not in res:
        return_message('No plugins found')

    plugins = res['response']

    headers = ['ID', 'Name', 'Type', 'Description', 'Family']
    mapped_plugins = [{
        'ID': p['id'],
        'Name': p['name'],
        'Type': p['type'],
        'Description': p['description'],
        'Family': p['family'].get('name')
    } for p in plugins]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Plugins', mapped_plugins, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Plugin(val.ID===obj.ID)': createContext(mapped_plugins, removeNull=True)
        }
    })


def get_vulnerabilities(client: Client, scan_results_id):
    query = client.create_query(scan_results_id, 'vulnipdetail')

    if not query or 'response' not in query:
        return 'Could not get vulnerabilites query'

    analysis = client.get_analysis(query['response']['id'], scan_results_id)

    client.delete_query(query.get('response', {}).get('id'))

    if not analysis or 'response' not in analysis:
        return 'Could not get vulnerabilites analysis'

    results = analysis['response']['results']

    if not results or len(results) == 0:
        return 'No vulnerabilities found'

    mapped_vulns = []

    for vuln in results:
        mapped_vuln = {
            'ID': vuln['pluginID'],
            'Name': vuln['name'],
            'Description': vuln['pluginDescription'],
            'Family': vuln['family'].get('name'),
            'Severity': vuln['severity'].get('name'),
            'Total': vuln['total']
        }

        mapped_vulns.append(mapped_vuln)

    sv_level = {
        'Critical': 4,
        'High': 3,
        'Medium': 2,
        'Low': 1,
        'Info': 0
    }

    mapped_vulns.sort(key=lambda r: sv_level[r['Severity']])

    return mapped_vulns


def get_vulnerability_command(client: Client, args: Dict[str, Any]):
    vuln_id = args.get('vulnerability_id')
    scan_results_id = args.get('scan_results_id')
    page = int(args.get('page', '0'))
    limit = int(args.get('limit', '50'))
    if limit > 200:
        limit = 200

    vuln_filter = [{
        'filterName': 'pluginID',
        'operator': '=',
        'value': vuln_id
    }]

    query = {
        'scanID': scan_results_id,
        'filters': vuln_filter,
        'tool': 'vulndetails',
        'type': 'vuln',
        'startOffset': page,  # Lower bound for the results list (must be specified)
        'endOffset': page + limit  # Upper bound for the results list (must be specified)
    }

    analysis = client.get_analysis(query, scan_results_id)

    if not analysis or 'response' not in analysis:
        return_error('Error: Could not get vulnerability analysis')

    results = analysis['response']['results']

    if not results or len(results) == 0:
        return_error('Error: Vulnerability not found in the scan results')

    vuln_response = client.get_vulnerability(vuln_id)

    if not vuln_response or 'response' not in vuln_response:
        return_message('Vulnerability not found')

    vuln = vuln_response['response']
    vuln['severity'] = results[0]['severity']  # The vulnerability severity is the same in all the results

    hosts = get_vulnerability_hosts_from_analysis(results)

    cves = None
    cves_output = []  # type: List[dict]
    if vuln.get('xrefs'):
        # Extract CVE
        cve_filter = list(filter(lambda x: x.strip().startswith('CVE'), vuln['xrefs'].split(',')))
        if cve_filter and len(cve_filter) > 0:
            cves = list(map(lambda c: c.replace('CVE:', '').strip(), cve_filter))
            cves_output += map(lambda c: {
                'ID': c
            }, cves)

    mapped_vuln = {
        'ID': vuln['id'],
        'Name': vuln['name'],
        'Description': vuln['description'],
        'Type': vuln['type'],
        'Severity': vuln['severity'].get('name'),
        'Synopsis': vuln['synopsis'],
        'Solution': vuln['solution']
    }

    vuln_info = {
        'Published': timestamp_to_utc(vuln['vulnPubDate']),
        'CPE': vuln['cpe'],
        'CVE': cves
    }

    exploit_info = {
        'ExploitAvailable': vuln['exploitAvailable'],
        'ExploitEase': vuln['exploitEase']
    }

    risk_info = {
        'RiskFactor': vuln['riskFactor'],
        'CVSSBaseScore': vuln['baseScore'],
        'CVSSTemporalScore': vuln['temporalScore'],
        'CVSSVector': vuln['cvssVector']
    }

    plugin_details = {
        'Family': vuln['family'].get('name'),
        'Published': timestamp_to_utc(vuln['pluginPubDate']),
        'Modified': timestamp_to_utc(vuln['pluginModDate']),
        'CheckType': vuln['checkType']
    }

    hr = '## Vulnerability: {} ({})\n'.format(mapped_vuln['Name'], mapped_vuln['ID'])
    hr += '### Synopsis\n{}\n### Description\n{}\n### Solution\n{}\n'.format(
        mapped_vuln['Synopsis'], mapped_vuln['Description'], mapped_vuln['Solution'])
    hr += tableToMarkdown('Hosts', hosts, removeNull=True)
    hr += tableToMarkdown('Risk Information', risk_info, removeNull=True)
    hr += tableToMarkdown('Exploit Information', exploit_info, removeNull=True)
    hr += tableToMarkdown('Plugin Details', plugin_details, removeNull=True)
    hr += tableToMarkdown('Vulnerability Information', vuln_info, removeNull=True)

    mapped_vuln.update(vuln_info)
    mapped_vuln.update(exploit_info)
    mapped_vuln.update(risk_info)
    mapped_vuln['PluginDetails'] = plugin_details
    mapped_vuln['Host'] = hosts

    scan_result = {
        'ID': scan_results_id,
        'Vulnerability': mapped_vuln,
    }

    context = {}

    context['TenableSC.ScanResults.Vulnerability(val.ID===obj.ID)'] = createContext(scan_result['Vulnerability'], removeNull=True)

    if len(cves_output) > 0:
        context['CVE(val.ID===obj.ID)'] = createContext(cves_output)

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': vuln_response,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': hr,
        'EntryContext': context
    })


def get_vulnerability_hosts_from_analysis(results):
    return [{
        'IP': host['ip'],
        'MAC': host['macAddress'],
        'Port': host['port'],
        'Protocol': host['protocol']
    } for host in results]


def delete_scan_command(client: Client, args: Dict[str, Any]):
    scan_id = args.get('scan_id')

    res = client.delete_scan(scan_id)

    if not res:
        return_error('Error: Could not delete the scan')

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['text'],
        'HumanReadable': 'Scan successfully deleted'
    })


def get_device_command(client: Client, args: Dict[str, Any]):
    uuid = args.get('uuid')
    ip = args.get('ip')
    dns_name = args.get('dns_name')
    repo = args.get('repository_id')

    res = client.get_device(uuid, ip, dns_name, repo)

    if not res or 'response' not in res:
        return_message('Device not found')

    device = res['response']

    headers = [
        'IP',
        'UUID',
        'MacAddress',
        'RepositoryID',
        'RepositoryName',
        'NetbiosName',
        'DNSName',
        'OS',
        'OsCPE',
        'LastScan',
        'TotalScore',
        'LowSeverity',
        'MediumSeverity',
        'HighSeverity',
        'CriticalSeverity'
    ]

    mapped_device = {
        'IP': device['ip'],
        'UUID': device.get('uuid'),
        'MacAddress': device.get('macAddress'),
        'RepositoryID': device.get('repository', {}).get('id'),
        'RepositoryName': device.get('repository', {}).get('name'),
        'NetbiosName': device.get('netbiosName'),
        'DNSName': device.get('dnsName'),
        'OS': re.sub('<[^<]+?>', ' ', device['os']).lstrip() if device.get('os') else '',
        'OsCPE': device.get('osCPE'),
        'LastScan': timestamp_to_utc(device['lastScan']),
        'TotalScore': device.get('total'),
        'LowSeverity': device.get('severityLow'),
        'MediumSeverity': device.get('severityMedium'),
        'HighSeverity': device.get('severityHigh'),
        'CriticalSeverity': device.get('severityCritical')
    }

    endpoint = {
        'IPAddress': mapped_device['IP'],
        'MACAddress': mapped_device['MacAddress'],
        'Hostname': mapped_device['DNSName'],
        'OS': mapped_device['OS']
    }

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Device', mapped_device, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Device(val.UUID===obj.UUID)': createContext(mapped_device, removeNull=True),
            'Endpoint(val.IP===obj.IP)': createContext(endpoint, removeNull=True)
        }
    })


def list_users_command(client: Client, args: Dict[str, Any]):
    user_id = args.get('id')
    username = args.get('username')
    email = args.get('email')

    res = client.get_users('id,username,firstname,lastname,title,email,createdTime,modifiedTime,lastLogin,role', user_id)

    if not res or 'response' not in res:
        return_message('No users found')

    users = res['response']

    if not isinstance(users, list):
        users = [users]

    if not user_id:
        if username:
            users = list(filter(lambda u: u['username'] == username, users))
        elif email:
            users = list(filter(lambda u: u['email'] == email, users))

    if len(users) == 0:
        return_message('No users found')

    headers = [
        'ID',
        'Username',
        'Firstname',
        'Lastname',
        'Title',
        'Email',
        'Created',
        'Modified',
        'LastLogin',
        'Role'
    ]

    mapped_users = [{
        'ID': u['id'],
        'Username': u['username'],
        'FirstName': u['firstname'],
        'LastName': u['lastname'],
        'Title': u['title'],
        'Email': u['email'],
        'Created': timestamp_to_utc(u['createdTime']),
        'Modified': timestamp_to_utc(u['modifiedTime']),
        'LastLogin': timestamp_to_utc(u['lastLogin']),
        'Role': u['role'].get('name')
    } for u in users]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Users', mapped_users, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.User(val.ID===obj.ID)': createContext(mapped_users, removeNull=True)
        }
    })


def get_system_licensing_command(client: Client, args: Dict[str, Any]):
    res = client.get_system_licensing()

    if not res or 'response' not in res:
        return_error('Error: Could not retrieve system licensing')

    status = res['response']

    mapped_licensing = {
        'License': status['licenseStatus'],
        'LicensedIPS': status['licensedIPs'],
        'ActiveIPS': status['activeIPs']
    }

    headers = [
        'License',
        'LicensedIPS',
        'ActiveIPS'
    ]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Licensing information',
                                         mapped_licensing, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Status': createContext(mapped_licensing, removeNull=True)
        }
    })


def get_system_information_command(client: Client, args: Dict[str, Any]):
    sys_res = client.get_system()

    if not sys_res or 'response' not in sys_res:
        return_error('Error: Could not retrieve system information')

    diag_res = client.get_system_diagnostics()

    if not diag_res or 'response' not in diag_res:
        return_error('Error: Could not retrieve system information')

    sys_res.update(diag_res)
    diagnostics = diag_res['response']
    system = sys_res['response']

    mapped_information = {
        'Version': system['version'],
        'BuildID': system['buildID'],
        'ReleaseID': system['releaseID'],
        'License': system['licenseStatus'],
        'RPMStatus': diagnostics['statusRPM'],
        'JavaStatus': diagnostics['statusJava'],
        'DiskStatus': diagnostics['statusDisk'],
        'DiskThreshold': diagnostics['statusThresholdDisk'],
        'LastCheck': timestamp_to_utc(diagnostics['statusLastChecked']),
    }

    headers = [
        'Version',
        'BuildID',
        'ReleaseID',
        'License',
        'RPMStatus',
        'JavaStatus',
        'DiskStatus',
        'DiskThreshold',
        'LastCheck'
    ]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': sys_res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc System information',
                                         mapped_information, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.System(val.BuildID===obj.BuildID)': createContext(mapped_information, removeNull=True)
        }
    })


def list_alerts_command(client: Client, args: Dict[str, Any]):
    res = client.get_alerts(fields='id,name,description,didTriggerLastEvaluation,lastTriggered,'
                            'action,lastEvaluated,ownerGroup,owner')
    manageable = args.get('manageable', 'false').lower()

    if not res or 'response' not in res or not res['response']:
        return_message('No alerts found')

    alerts = get_elements(res['response'], manageable)

    if len(alerts) == 0:
        return_message('No alerts found')

    headers = ['ID', 'Name', 'Actions', 'State', 'LastTriggered', 'LastEvaluated', 'Group', 'Owner']
    mapped_alerts = [{
        'ID': a['id'],
        'Name': a['name'],
        'State': 'Triggered' if a['didTriggerLastEvaluation'] == 'true' else 'Not Triggered',
        'Actions': demisto.dt(a['action'], 'type'),
        'LastTriggered': timestamp_to_utc(a['lastTriggered'], default_returned_value='Never'),
        'LastEvaluated': timestamp_to_utc(a['lastEvaluated']),
        'Group': a['ownerGroup'].get('name'),
        'Owner': a['owner'].get('username')
    } for a in alerts]

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': tableToMarkdown('Tenable.sc Alerts', mapped_alerts, headers=headers, removeNull=True),
        'EntryContext': {
            'TenableSC.Alert(val.ID===obj.ID)': createContext(mapped_alerts, removeNull=True)
        }
    })


def get_alert_command(client: Client, args: Dict[str, Any]):
    alert_id = args.get('alert_id')
    res = client.get_alerts(alert_id=alert_id)

    if not res or 'response' not in res or not res['response']:
        return_message('Alert not found')

    alert = res['response']
    query_res = client.get_query(alert['query'].get('id'))
    query = query_res.get('response')

    alert_headers = ['ID', 'Name', 'Description', 'LastTriggered', 'State', 'Behavior', 'Actions']
    query_headers = ['Trigger', 'Query']
    action_headers = ['Type', 'Values']

    filter_headers = ['Name', 'Values']
    mapped_alert = {
        'ID': alert['id'],
        'Name': alert['name'],
        'Description': alert['description'],
        'LastTriggered': timestamp_to_utc(alert['lastTriggered'], default_returned_value='Never'),
        'State': 'Triggered' if alert['didTriggerLastEvaluation'] == 'true' else 'Not Triggered',
        'Behavior': 'Execute on every trigger ' if alert['executeOnEveryTrigger'] == 'true' else 'Execute only on'
                                                                                                 ' first trigger'
    }

    mapped_condition = {
        'Trigger': '{} {} {}'.format(alert['triggerName'], alert['triggerOperator'], alert['triggerValue']),
        'Query': alert['query'].get('name')
    }

    mapped_filters = None
    if query:
        mapped_filters = [{
            'Name': f['filterName'],
            'Values': demisto.dt(f['value'], 'name') if isinstance(f['value'], list) else f['value']
        } for f in query.get('filters', [])]
        mapped_condition['Filter'] = mapped_filters

    mapped_actions = [{
        'Type': a['type'],
        'Values': demisto.dt(a, '{}.{}'.format('definition', ACTION_TYPE_TO_VALUE[a['type']]))
    } for a in alert['action']]

    hr = tableToMarkdown('Tenable.sc Alert', mapped_alert, headers=alert_headers, removeNull=True)
    hr += tableToMarkdown('Condition', mapped_condition, headers=query_headers, removeNull=True)
    if mapped_filters:
        hr += tableToMarkdown('Filters', mapped_filters, headers=filter_headers, removeNull=True)
    if mapped_actions:
        hr += tableToMarkdown('Actions', mapped_actions, headers=action_headers, removeNull=True)
        mapped_alert['Action'] = mapped_actions

    mapped_alert['Condition'] = mapped_condition

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': hr,
        'EntryContext': {
            'TenableSC.Alert(val.ID===obj.ID)': createContext(mapped_alert, removeNull=True)
        }
    })


def fetch_incidents(client: Client, first_fetch: str = '3 days'):
    incidents = []
    last_run = demisto.getLastRun()
    if not last_run:
        last_run = {}
    if 'time' not in last_run:
        # get timestamp in seconds
        timestamp, _ = parse_date_range(first_fetch, to_timestamp=True)
        timestamp /= 1000
    else:
        timestamp = last_run['time']

    max_timestamp = timestamp
    res = client.get_alerts(
        fields='id,name,description,lastTriggered,triggerName,triggerOperator,'
               'triggerValue,action,query,owner,ownerGroup,schedule,canManage')

    alerts = get_elements(res.get('response', {}), manageable='false')
    for alert in alerts:
        # 0 corresponds to never triggered
        if int(alert.get('lastTriggered', 0)) > timestamp:
            incidents.append({
                'name': 'Tenable.sc Alert Triggered - ' + alert['name'],
                'occurred': timestamp_to_utc(alert['lastTriggered']),
                'rawJSON': json.dumps(alert)
            })

            if int(alert['lastTriggered']) > max_timestamp:
                max_timestamp = int(alert['lastTriggered'])

    demisto.incidents(incidents)
    demisto.setLastRun({'time': max_timestamp})


def get_all_scan_results_command(client: Client, args: Dict[str, Any]):
    res = client.get_all_scan_results()
    get_manageable_results = args.get('manageable', 'false').lower()  # 'true' or 'false'
    page = int(args.get('page', '0'))
    limit = int(args.get('limit', '50'))
    if limit > 200:
        limit = 200

    if not res or 'response' not in res or not res['response']:
        return_message('Scan results not found')

    elements = get_elements(res['response'], get_manageable_results)

    headers = ['ID', 'Name', 'Status', 'Description', 'Policy', 'Group', 'Owner', 'ScannedIPs',
               'StartTime', 'EndTime', 'Duration', 'Checks', 'ImportTime', 'RepositoryName']

    scan_results = [{
        'ID': elem['id'],
        'Name': elem['name'],
        'Status': elem['status'],
        'Description': elem.get('description', None),
        'Policy': elem['details'],
        'Group': elem.get('ownerGroup', {}).get('name'),
        'Checks': elem.get('completedChecks', None),
        'StartTime': timestamp_to_utc(elem['startTime']),
        'EndTime': timestamp_to_utc(elem['finishTime']),
        'Duration': scan_duration_to_demisto_format(elem['scanDuration']),
        'ImportTime': timestamp_to_utc(elem['importStart']),
        'ScannedIPs': elem['scannedIPs'],
        'Owner': elem['owner'].get('username'),
        'RepositoryName': elem['repository'].get('name')
    } for elem in elements[page:page + limit]]

    readable_title = 'Tenable.sc Scan results - {0}-{1}'.format(page, page + limit - 1)
    hr = tableToMarkdown(readable_title, scan_results, headers, removeNull=True,
                         metadata='Total number of elements is {}'.format(len(elements)))

    demisto.results({
        'Type': entryTypes['note'],
        'Contents': res,
        'ContentsFormat': formats['json'],
        'ReadableContentsFormat': formats['markdown'],
        'HumanReadable': hr,
        'EntryContext': {
            'TenableSC.ScanResults(val.ID===obj.ID)': createContext(scan_results, removeNull=True)
        }
    })


def timestamp_to_utc(timestamp_str, default_returned_value=''):
    if timestamp_str and (int(timestamp_str) > 0):  # no value is when timestamp_str == '-1'
        return datetime.utcfromtimestamp(int(timestamp_str)).strftime(
            '%Y-%m-%dT%H:%M:%SZ')
    return default_returned_value


def scan_duration_to_demisto_format(duration, default_returned_value=''):
    if duration:
        return float(duration) / 60
    return default_returned_value


def main():
    params = demisto.params()
    command = demisto.command()
    args = demisto.args()
    verify_ssl = not params.get('unsecure', False)
    proxy = params.get('proxy', False)
    user_name = params.get('credentials', {}).get('identifier')
    password = params.get('credentials', {}).get('password')
    access_key = params.get('creds_keys', {}).get('identifier')
    secret_key = params.get('creds_keys', {}).get('password')
    url = params.get('server')

    demisto.info(f'Executing command {command}')

    command_dict = {
        'tenable-sc-list-scans': list_scans_command,
        'tenable-sc-list-policies': list_policies_command,
        'tenable-sc-list-repositories': list_repositories_command,
        'tenable-sc-list-credentials': list_credentials_command,
        'tenable-sc-list-zones': list_zones_command,
        'tenable-sc-list-report-definitions': list_report_definitions_command,
        'tenable-sc-list-assets': list_assets_command,
        'tenable-sc-list-plugins': list_plugins_command,
        'tenable-sc-get-asset': get_asset_command,
        'tenable-sc-create-asset': create_asset_command,
        'tenable-sc-delete-asset': delete_asset_command,
        'tenable-sc-create-scan': create_scan_command,
        'tenable-sc-launch-scan': launch_scan_command,
        'tenable-sc-get-scan-status': get_scan_status_command,
        'tenable-sc-get-scan-report': get_scan_report_command,
        'tenable-sc-get-vulnerability': get_vulnerability_command,
        'tenable-sc-delete-scan': delete_scan_command,
        'tenable-sc-get-device': get_device_command,
        'tenable-sc-list-users': list_users_command,
        'tenable-sc-list-alerts': list_alerts_command,
        'tenable-sc-get-alert': get_alert_command,
        'tenable-sc-get-system-information': get_system_information_command,
        'tenable-sc-get-system-licensing': get_system_licensing_command,
        'tenable-sc-get-all-scan-results': get_all_scan_results_command
    }

    try:
        client = Client(
            verify_ssl=verify_ssl,
            proxy=proxy,
            user_name=user_name,
            password=password,
            access_key=access_key,
            secret_key=secret_key,
            url=url
        )

        if command == 'test-module':
            demisto.results('ok')
        elif command == 'fetch-incidents':
            first_fetch = params.get('fetch_time').strip()
            fetch_incidents(client, first_fetch)
        else:
            command_dict[command](client, args)
    except Exception as e:
        return_error(
            f'Failed to execute {command} command. Error: {str(e)}'
        )
    finally:
        if (user_name and password) and not (access_key and secret_key):
            client.logout()


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
