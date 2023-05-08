
var serverURL = params.url;
if (serverURL.slice(-1) === '/') {
    serverURL = serverURL.slice(0,-1);
}

if (params.auth_id || (params.creds_apikey && params.creds_apikey.identifier)) {
    if (!serverURL.endsWith('/xsoar')){
        serverURL = serverURL + '/xsoar'
    }
}

var marketplace_url = params.marketplace_url? params.marketplace_url : 'https://marketplace.xsoar.paloaltonetworks.com/'

getTenantAccountName = function () {
    // example: for 'https://account-testing-ysdkvou:443/acc_Test' will return 'acc_Test'
    const urls = demistoUrls()
    const server_url = urls['server'].toString()
    // server_url example - https://account-testing-ysdkvou:443/acc_Test
    var account_name = ''
    // check if server_url contains "/acc_" string
    if (server_url.indexOf("/acc_") >= 0){
        const words = server_url.split('acc_')
        const tenant_name = words[words.length - 1]
        if (tenant_name !== "") {
            account_name = 'acc_' + tenant_name
        }
    }
    return account_name
}

getStandardAuthMethodHeaders = function(key, auth_id, content_type) {
    return {
                'Authorization': [key],
                'x-xdr-auth-id': [auth_id],
                'Content-Type': [content_type],
                'Accept': ['application/json']
            }
}

getAdvancedAuthMethodHeaders = function(key, auth_id, content_type,) {
    const nonce = Array.from({length: 64}, () => Math.random().toString(36).charAt(2)).join("");
    const timestamp = Date.now().toString();
    var auth_key = key + nonce + timestamp
    auth_key = unescape(encodeURIComponent(auth_key));
    const auth_key_hash = SHA256_hash(auth_key)

    return {
                'x-xdr-timestamp': [timestamp],
                'x-xdr-nonce': [nonce],
                'x-xdr-auth-id': [auth_id],
                'Authorization': [auth_key_hash],
                'Content-Type': [content_type],
                'Accept': ['application/json']
            }
    }

getRequestURL = function (uri) {
    var requestUrl = serverURL;
    if (params.use_tenant){
        requestUrl += '/' + getTenantAccountName();
    }
    if (uri.slice(0, 1) !== '/') {
        requestUrl += '/';
    }
    requestUrl += uri;
    return requestUrl
}

sendMultipart = function (uri, entryID, body) {
    var requestUrl = getRequestURL(uri)
    try {
        body = JSON.parse(body);
    } catch (ex) {
        // do nothing, use the body as is in the request.
        logDebug('could not parse body as a JSON object, passing as is. body: ' + JSON.stringify(body));
    }
    var key = params.apikey? params.apikey : (params.creds_apikey? params.creds_apikey.password : '');
    if (key == ''){
        throw 'API Key must be provided.';
    }
    var auth_id = params.auth_id? params.auth_id : (params.creds_apikey? params.creds_apikey.identifier : '');
    var headers = {}
    // in case the integration was installed before auth_method was added, the auth_method param will be empty so
    // we will use the standard auth method
    if (!params.auth_method || params.auth_method == 'Standard'){
        headers = getStandardAuthMethodHeaders(key, auth_id, 'multipart/form-data')
    }
    else if (params.auth_method == 'Advanced') {
        headers = getAdvancedAuthMethodHeaders(key, auth_id, 'multipart/form-data')
    }
    var res = httpMultipart(
        requestUrl,
        entryID,
        {
            Headers: headers,
        },
        body,
        params.insecure,
        params.proxy,
        undefined,
        'file'
    );
    if (res.StatusCode < 200 || res.StatusCode >= 300) {
        throw 'Core REST APIs - Request Failed.\nStatus code: ' + res.StatusCode + '.\nBody: ' + JSON.stringify(res) + '.';
    }
    try {
        var response = res.Body;
        try {
            response = JSON.parse(res.Body);
        } catch (ex) {
            // do nothing, already handled prior the try/catch
        }
        return {response: response};
    } catch (ex) {
        throw 'Core REST APIs - Error parsing response - ' + ex + '\nBody:' + res.Body;
    }

};

var sendRequest = function(method, uri, body, raw) {
    var requestUrl = getRequestURL(uri)
    log(requestUrl);
    var key = params.apikey? params.apikey : (params.creds_apikey? params.creds_apikey.password : '');
    if (key == ''){
        throw 'API Key must be provided.';
    }
    var auth_id = params.auth_id? params.auth_id : (params.creds_apikey? params.creds_apikey.identifier : '');
    var headers = {}
    // in case the integration was installed before auth_method was added, the auth_method param will be empty so
    // we will use the standard auth method
    if (!params.auth_method || params.auth_method == 'Standard'){
        headers = getStandardAuthMethodHeaders(key, auth_id, 'application/json')
    }
    else if (params.auth_method == 'Advanced') {
        if (!auth_id) {
            throw 'Core REST APIs - please choose "Standard Authentication method" or provide the API Key ID.';
        }
        headers = getAdvancedAuthMethodHeaders(key, auth_id, 'application/json')
    }
    var res = http(
        requestUrl,
        {
            Method: method,
            Headers: headers,
            Body: body,
            SaveToFile: raw
        },
        params.insecure,
        params.proxy
    );

    if (res.StatusCode < 200 || res.StatusCode >= 300) {
        throw 'Core REST APIs - Request Failed.\nStatus code: ' + res.StatusCode + '.\nBody: ' + JSON.stringify(res) + '.';
    }
    if (raw) {
        return res;
    } else {
        try {
            var response = res.Body;
            try {
                response = JSON.parse(res.Body);
            } catch (ex) {
                // do nothing, already handled prior the try/catch
            }
            return {response: response};
        } catch (ex) {
            throw 'Core REST APIs - Error parsing response - ' + ex + '\nBody:' + res.Body;
        }
    }
};
/* helper functions */
function generate_uuidv4() {
    var d = new Date().getTime();//Timestamp
    var d2 = ((typeof performance !== 'undefined') && performance.now && (performance.now()*1000)) || 0;//Time in microseconds since page-load or 0 if unsupported
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16;//random number between 0 and 16
        if(d > 0){//Use timestamp until depleted
            r = (d + r)%16 | 0;
            d = Math.floor(d/16);
        } else {//Use microseconds since page-load if supported
            r = (d2 + r)%16 | 0;
            d2 = Math.floor(d2/16);
        }
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
}


function reduce_one_entry(data, keep_fields) {
    var new_d = {};
    for (var field_index = 0; field_index < keep_fields.length; field_index += 1) {
        var field = keep_fields[field_index];
        if (data[field]) {
            new_d[field] = data[field];
        }
    }
    return new_d;
}

function reduce_data(data, fields_to_keep) {
    if (data instanceof Array) {
        var new_data = [];
        for (var data_index = 0; data_index < data.length; data_index += 1) {
            var d = data[data_index];
            new_data.push(reduce_one_entry(d, fields_to_keep));
        }
        return new_data;
    }
    else {
        if (data.constructor == Object) {
            return [reduce_one_entry(data, fields_to_keep)];
        }
    }
    return data;
}

var deleteIncidents = function(ids_to_delete, fields_to_keep) {
    var body = {
        ids: ids_to_delete,
        all: false,
        filter: {}
    };

    var res = sendRequest('POST', '/incident/batchDelete', JSON.stringify(body));
    if (isError(res[0])) {
        throw res[0].Contents;
    }

    var response = res['response'];
    if (fields_to_keep && (fields_to_keep != "all")) {
        response['data'] = reduce_data(response['data'], fields_to_keep);
    }
    var md = tableToMarkdown('Core delete incidents', response, ['data', 'total', "notUpdated"]);

    return {
        ContentsFormat: formats.json,
        Type: entryTypes.note,
        Contents: res,
        HumanReadable: md
    };
};

var installPack = function(pack_url, entry_id, skip_verify, skip_validation){
    let file_path;
    if (entry_id){
        file_path = entry_id;
    }
    else{
        // download pack zip file
        var res = http(
        pack_url,
        {
            Method: 'GET',
            Headers: {},
            SaveToFile: true
        });

        if (res.StatusCode < 200 || res.StatusCode >= 300) {
            throw 'Core REST APIs - Failed to download pack file from ' + pack_url;
        }
        file_path = res.Path;
    }

    let upload_url = 'contentpacks/installed/upload?'

    // set the skipVerify parameter
    if(isDemistoVersionGE('6.5.0')){
        if (skip_verify && skip_verify === 'false') {
            upload_url+='skipVerify=false'
        }else{
            upload_url+='skipVerify=true'
        }
    }

    // set the skipValidation parameter
    if(isDemistoVersionGE('6.6.0')){
        if (skip_validation && skip_validation === 'false') {
            upload_url+='&skipValidation=false'
        }else{
            upload_url+='&skipValidation=true'
        }
    }
    // upload the pack
    sendMultipart(upload_url, file_path,'{}');
};

var installPacks = function(packs_to_install, file_url, entry_id, skip_verify, skip_validation) {
    if ((!packs_to_install) && (!file_url) && (!entry_id)) {
        throw 'Either packs_to_install, file_url or entry_id argument must be provided.';
    }
    else if (file_url) {
        installPack(file_url, undefined, skip_verify, skip_validation)
        logDebug('Pack installed successfully from ' + file_url)
        return 'The pack installed successfully from the file ' + file_url
    }
    else if (entry_id) {
        installPack(undefined, entry_id, skip_verify, skip_validation)
        logDebug('The pack installed successfully from the file.')
        return 'The pack installed successfully from the file.'
    }
    else{
        let installed_packs = []
        let packs = JSON.parse(packs_to_install);

        for (let pack_index = 0; pack_index < packs.length; pack_index += 1) {
            let pack = packs[pack_index];
            let pack_id = Object.keys(pack)[0]
            let pack_version = pack[pack_id]

            let pack_url = '{0}{1}/{2}/{3}.zip'.format(marketplace_url,pack_id,pack_version,pack_id)
            installPack(pack_url, undefined, skip_verify, skip_validation)
            logDebug(pack_id + ' pack installed successfully')
            installed_packs.push(pack_id)
        }

        return 'The following packs installed successfully: ' + installed_packs.join(", ")
    }
};

var fileUploadCommand = function(incident_id, file_content, file_name, entryID, target ) {
    /**
     * Upload a new file
    Arguments:
        @param {String} incident_id  -- incident id to upload the file to
        @param {String} file_content -- content of the file to upload
        @param {String} file_name  -- name of the file in the dest incident
        @param {String} entryID  -- entry ID of the file
        @param {String} target -- upload the file as an attachment or an war room entry
    Returns:
        CommandResults -- Readable output
    Note:
        You can give either the entryID or file_name.
    """
     */
    if ((!file_name) && (!entryID)) {
        throw 'Either file_name or entry_id argument must be provided.';
    }
    var service = (target == 'Incident Attachment') ? 'incident' : 'entry';
    var body = {
        files : {
            file: {
                value: file_content,
                options: {
                    filename: file_name,
                    contentType: 'application/octet-stream'
                }
            }
        }
    };
    var res = sendRequest('POST', `/${service}/upload/${incident_id}/attachment`, JSON.stringify(body));
    if (isError(res[0])) {
        throw res[0].Contents;
    }

    var md = `File ${file_name} uploaded successfully to incident ${incident_id}.`;
    // in case the file uploaded as war room entry
    if (target === 'war room entry') {
        md += ` Entry ID is ${res['response'].entries[0].id}`;
    }
    return {
        Type: entryTypes.file,
        FileID: saveFile(file_content),
        File: file_name,
        Contents: file_content,
        HumanReadable: md
    };
};

var get_incident_id = function (entry_id) {
    const res = entry_id.match(/(\d+)@(\d+)/);
    if (!res) {
        throw new Error("EntryID unknown or malformatted!");
    }
    return res[0][1];
};

var fileDelete = function(entryId) {
    /**
     * Upload a new file
    Arguments:
        @param {String} entryId  -- entry ID of the file
    Returns:
        Message that the file was deleted successfully + entry_id
    """
     */
    //let files = demisto.context().File || [];
    //files = Array.isArray(files) ? files : [files];
    logDebug('mm-whta')
    
    try { 
        var body = {
            id: entryId
        };
        logDebug('mm-whta')
        const response = sendRequest('POST',`/Indicators/delete/v2/${entryId}`, JSON.stringify(body));
    } catch (e) {
        throw new Error(`File already deleted or not found!\n${e}`);
    }
    logDebug('mm-whta')
    const incident_id = get_incident_id(entryId);
    bodyContent = {
        id: "",
        version: 0,
        investigationId: incident_id,
        data: `!DeleteContext key=${'File'}`,
        args: null,
        markdown: false
    };
    sendRequest('DELETE','/entry', JSON.stringify(bodyContent));
    //sleep(1000); // To let the API execute the request
    
    //const new_files = files.filter(file => file.EntryID !== entryId);
    return "new_files";

};

function fileDeleteCommand(entryId) {
    const new_files = fileDelete(entryId);
    logDebug('mm-the fuck')
    console.log('mm- nuuu')
    return {
        readable_output: `File ${entryId} deleted!`,
        outputs: new_files,
        outputs_prefix: "File"
    };
}



function coreApiFileCheckCommand(entryId) {
    
    if (Object.keys(output_content).length > 0) {
        //client.delete_context(demisto.incident.id, 'IsFileExists');
        sleep(1000); // To let the API execute the request
    }
    
    try {
        //const path_res = demisto.getFilePath(entryId);
        const file_name = path_res.name;
        
        const output_tmp = { [entryId]: true };
        //output_content = { ...output_content, ...output_tmp };
        
        return {
            readable_output: `File ${entryId} exists under the name ${file_name}!`,
            outputs_prefix: 'IsFileExists',
            outputs: output_content
        };
    } catch (err) {
        const output_tmp = { [entryId]: false };
        //output_content = { ...output_content, ...output_tmp };
        
        return {
            readable_output: `File ${entryId} does not exist! ${err}`,
            outputs_prefix: 'IsFileExists',
            outputs: output_content
        };
    }
}

switch (command) {
    case 'test-module':
        sendRequest('GET','user');
        return 'ok';
    case 'demisto-api-post':
    case 'core-api-post':
        if(args.body)
            var body = JSON.parse(args.body);
        else
            logDebug('The body is empty.')

        return sendRequest('POST',args.uri, args.body);
    case 'demisto-api-get':
    case 'core-api-get':
        return sendRequest('GET',args.uri);
    case 'demisto-api-put':
    case 'core-api-put':
        var body = JSON.parse(args.body);
        return sendRequest('PUT',args.uri, args.body);
    case 'demisto-api-delete':
    case 'core-api-delete':
        return sendRequest('DELETE',args.uri);
    case 'demisto-api-multipart':
    case 'core-api-multipart':
        return sendMultipart(args.uri, args.entryID, args.body);
    case 'demisto-api-download':
    case 'core-api-download':
        var res = sendRequest('GET',args.uri,args.body,true);
        var filename = res.Path;
        if (args.filename) {
            filename = args.filename;
        } else {
            var disposition = res.Headers['Content-Disposition'][0].split('=');
            if (disposition.length === 2) {
                filename = disposition[1];
            }
        }
        var desc = args.description || '';
        return ({Type: entryTypes.file, FileID: res.Path, File: filename, Contents: desc});
    case 'demisto-delete-incidents':
    case 'core-delete-incidents':
        var ids = argToList(args.ids);
        var fields = argToList(args.fields);
        return deleteIncidents(ids, fields);
    case 'demisto-api-install-packs':
    case 'core-api-install-packs':
        return installPacks(args.packs_to_install, args.file_url, args.entry_id, args.skip_verify, args.skip_validation);
    case 'core-api-file-upload':
        return fileUploadCommand(args.incident_id, args.file_content, args.file_name, args.entryID, args.target)
    case 'core-api-file-delete':
        logDebug('mm-whta')
        return fileDeleteCommand(args.entry_id);
    case 'core-api-file-attachment-delete':
        return null
    case 'core-api-file-check':
        return coreApiFileCheckCommand(args.entry_id);
    default:
        throw 'Core REST APIs - unknown command';
}
