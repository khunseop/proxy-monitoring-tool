// URL 및 탭 기반 섹션 표시
function switchProxySection(section) {
    if (section === 'list') {
        $('#proxy-list-tab').addClass('is-active');
        $('#proxy-groups-tab').removeClass('is-active');
        $('#proxy-list-section').show();
        $('#proxy-groups-section').hide();
        // URL 업데이트 (히스토리 저장 없이)
        window.history.replaceState(null, '', '/proxy');
    } else {
        $('#proxy-list-tab').removeClass('is-active');
        $('#proxy-groups-tab').addClass('is-active');
        $('#proxy-list-section').hide();
        $('#proxy-groups-section').show();
        // URL 업데이트
        window.history.replaceState(null, '', '/proxy/groups');
    }
}

// 초기화 함수
function initProxyPage() {
    // URL 경로에 따라 초기 섹션 설정
    const path = window.location.pathname;
    if (path === '/proxy/groups') {
        switchProxySection('groups');
    } else {
        switchProxySection('list');
    }
    
    loadProxies();
    loadGroups();

    // 검색/그룹 필터 (PJAX 재진입 대비 네임스페이스 바인딩)
    $(document).off('input.proxyFilter', '#proxySearchInput').on('input.proxyFilter', '#proxySearchInput', renderProxyTable);
    $(document).off('change.proxyFilter', '#proxyGroupFilter').on('change.proxyFilter', '#proxyGroupFilter', renderProxyTable);

    // 일괄등록: textarea 입력 미리보기(디바운스) + 파일 읽기
    $(document).off('input.proxyBulk', '#bulkProxyInput').on('input.proxyBulk', '#bulkProxyInput', () => {
        clearTimeout(window._bulkPreviewTimer);
        window._bulkPreviewTimer = setTimeout(renderBulkPreview, 300);
    });
    $(document).off('change.proxyBulk', '#bulkProxyFile').on('change.proxyBulk', '#bulkProxyFile', function () {
        const file = this.files && this.files[0];
        $('#bulkProxyFileName').text(file ? file.name : '선택된 파일 없음');
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (e) => {
            $('#bulkProxyInput').val(e.target.result || '');
            renderBulkPreview();
        };
        reader.onerror = () => (window.AppToast && AppToast.error('파일을 읽는데 실패했습니다.'));
        reader.readAsText(file);
    });
}

// 모달 관리
function openModal(type, id = null) {
    const modalId = `${type}Modal`;
    if (id) {
        const endpoint = type === 'proxy' ? 'proxies' : 'proxy-groups';
        $.get(`/api/${endpoint}/${id}`)
            .done(data => fillForm(type, data))
            .fail(() => (window.AppUtils && AppUtils.showError('데이터를 불러오는데 실패했습니다.')));
    } else {
        try { document.getElementById(`${type}Form`).reset(); } catch (e) {}
        $(`#${type}Id`).val('');
    }
    $(`#${modalId}`).addClass('is-active');
}

function closeModal(type) {
    $(`#${type}Modal`).removeClass('is-active');
}

function fillForm(type, data) {
    if (type === 'proxy') {
        $('#proxyId').val(data.id);
        $('#host').val(data.host);
        $('#username').val(data.username);
        $('#password').val('');
        $('#traffic_log_path').val(data.traffic_log_path || '');
        $('#group_id').val(data.group_id || '');
        $('#description').val(data.description || '');
        $('#is_active').prop('checked', data.is_active);

        // OID 개별 설정 초기화
        $('#proxyInterfaceList').empty();
        $('#use_custom_oids').prop('checked', false);
        $('#customOidSection').hide();

        if (data.oids_json) {
            try {
                const oids = JSON.parse(data.oids_json);
                if (oids && oids.__interface_oids__ && Object.keys(oids.__interface_oids__).length > 0) {
                    $('#use_custom_oids').prop('checked', true);
                    $('#customOidSection').show();
                    
                    for (const [name, config] of Object.entries(oids.__interface_oids__)) {
                        const in_oid = typeof config === 'string' ? config : (config.in_oid || '');
                        const out_oid = typeof config === 'object' ? (config.out_oid || '') : '';
                        addProxyInterfaceRow(name, in_oid, out_oid);
                    }
                }
            } catch (e) {
                console.error('Failed to parse oids_json:', e);
            }
        }
    } else {
        $('#groupId').val(data.id);
        $('#groupName').val(data.name);
        $('#groupDescription').val(data.description || '');
    }
}

function toggleCustomOids() {
    const isChecked = $('#use_custom_oids').is(':checked');
    if (isChecked) {
        $('#customOidSection').show();
        if ($('#proxyInterfaceList tr').length === 0) {
            addProxyInterfaceRow();
        }
    } else {
        $('#customOidSection').hide();
    }
}

function addProxyInterfaceRow(name = '', in_oid = '', out_oid = '') {
    const row = `
        <tr style="border-bottom: 1px solid var(--color-border-light);">
            <td style="padding: 0.5rem 0.25rem;"><input class="input is-small if-name" type="text" value="${name}" placeholder="eth0" style="background: white;"></td>
            <td style="padding: 0.5rem 0.25rem;"><input class="input is-small if-in-oid" type="text" value="${in_oid}" placeholder="1.3.6.1..." style="background: white;"></td>
            <td style="padding: 0.5rem 0.25rem;"><input class="input is-small if-out-oid" type="text" value="${out_oid}" placeholder="1.3.6.1..." style="background: white;"></td>
            <td class="has-text-centered" style="padding: 0.5rem 0.25rem;">
                <button class="button is-small is-ghost px-0 py-0" onclick="$(this).closest('tr').remove()" title="삭제" style="color: var(--color-text-muted); height: auto; font-weight: bold;">
                    <span>삭제</span>
                </button>
            </td>
        </tr>
    `;
    $('#proxyInterfaceList').append(row);
}

// 프록시 관리
let allProxies = [];

function loadProxies() {
    $.get('/api/proxies', { limit: 500 })
        .done(proxies => {
            allProxies = proxies || [];
            renderProxyTable();
        })
        .fail(() => (window.AppUtils && AppUtils.showError('프록시 목록을 불러오는데 실패했습니다.')));

    // 그룹 선택 옵션 업데이트 (등록 폼 + 목록 필터)
    $.get('/api/proxy-groups')
        .done(groups => {
            const select = $('#group_id');
            select.find('option:gt(0)').remove();
            groups.forEach(group => {
                select.append(`<option value="${group.id}">${group.name}</option>`);
            });

            const filter = $('#proxyGroupFilter');
            const selected = filter.val();
            filter.find('option:gt(0)').remove();
            groups.forEach(group => {
                filter.append(`<option value="${group.id}">${group.name}</option>`);
            });
            filter.val(selected || '');
        });
}

function renderProxyTable() {
    const tbody = $('#proxyTableBody');
    tbody.empty();

    const query = ($('#proxySearchInput').val() || '').trim().toLowerCase();
    const groupId = $('#proxyGroupFilter').val();

    const filtered = allProxies.filter(proxy => {
        if (query && !(proxy.host || '').toLowerCase().includes(query)) return false;
        if (groupId && String(proxy.group_id || '') !== groupId) return false;
        return true;
    });

    const rowCount = $('#proxyRowCount');
    rowCount.text(`${filtered.length} / ${allProxies.length}대`).show();

    if (filtered.length === 0) {
        const message = allProxies.length === 0 ? '등록된 프록시가 없습니다.' : '조건에 맞는 프록시가 없습니다.';
        tbody.append(`<tr><td colspan="5" class="has-text-centered has-text-grey py-5">${message}</td></tr>`);
        return;
    }

    filtered.forEach(proxy => {
        const statusTag = proxy.is_active
            ? '<span class="tag is-success is-light" style="font-weight:600;">활성</span>'
            : '<span class="tag is-danger is-light" style="font-weight:600;">비활성</span>';

        const hasCustomOid = (() => {
            if (!proxy.oids_json) return false;
            try { return !!JSON.parse(proxy.oids_json).__interface_oids__; } catch(e) { return false; }
        })();
        const oidBadge = hasCustomOid ? '<span class="tag is-warning is-light ml-2" style="font-size:0.65rem; vertical-align: middle;">개별 OID</span>' : '';

        tbody.append(`
            <tr>
                <td class="px-5 py-3 has-text-weight-semibold">${proxy.host}${oidBadge}</td>
                <td class="py-3">${proxy.group_name || '<span class="has-text-grey-light">없음</span>'}</td>
                <td class="has-text-centered py-3">${statusTag}</td>
                <td class="py-3 is-size-7 has-text-grey">${proxy.description || ''}</td>
                <td class="has-text-centered py-2" style="white-space: nowrap;">
                    <div class="buttons has-addons is-centered mb-0" style="flex-wrap: nowrap; justify-content: center;">
                        <button class="button is-subtle is-small" onclick="openModal('proxy', ${proxy.id})">수정</button>
                        <button class="button is-subtle is-small" onclick="cloneProxy(${proxy.id})">복제</button>
                        <button class="button is-subtle-danger is-small" onclick="deleteProxy(${proxy.id})">삭제</button>
                    </div>
                </td>
            </tr>
        `);
    });
}

function saveProxy() {
    const proxyId = $('#proxyId').val();
    const password = $('#password').val();
    
    // OID 개별 설정 수집
    let oids_json = null;
    if ($('#use_custom_oids').is(':checked')) {
        const interfaceOids = {};
        $('#proxyInterfaceList tr').each(function() {
            const name = $(this).find('.if-name').val().trim();
            const in_oid = $(this).find('.if-in-oid').val().trim();
            const out_oid = $(this).find('.if-out-oid').val().trim();
            if (name && (in_oid || out_oid)) {
                interfaceOids[name] = { in_oid, out_oid };
            }
        });
        
        if (Object.keys(interfaceOids).length > 0) {
            oids_json = JSON.stringify({
                __interface_oids__: interfaceOids
            });
        }
    }

    const data = {
        host: $('#host').val(),
        username: $('#username').val(),
        traffic_log_path: ($('#traffic_log_path').val() || '').trim(),
        is_active: $('#is_active').is(':checked'),
        group_id: $('#group_id').val() ? parseInt($('#group_id').val()) : null,
        oids_json: oids_json,
        description: $('#description').val() || null
    };

    if (!proxyId) {
        if (!password || password.length === 0) {
            if (window.AppUtils) AppUtils.showError('비밀번호는 필수입니다.');
            return;
        }
        data.password = password;
    } else if (password) {
        data.password = password;
    }

    const method = proxyId ? 'PUT' : 'POST';
    const url = proxyId ? `/api/proxies/${proxyId}` : '/api/proxies';

    $.ajax({
        url,
        method,
        contentType: 'application/json',
        data: JSON.stringify(data),
        success: () => {
            if (window.AppToast) AppToast.success(proxyId ? '프록시가 수정되었습니다.' : '프록시가 등록되었습니다.');
            loadProxies();
            closeModal('proxy');
        },
        error: (xhr) => (window.AppUtils && AppUtils.showError(extractErrorDetail(xhr, '저장에 실패했습니다.')))
    });
}

function deleteProxy(id) {
    if (confirm('정말 삭제하시겠습니까?')) {
        $.ajax({
            url: `/api/proxies/${id}`,
            method: 'DELETE',
            success: () => {
                if (window.AppToast) AppToast.success('프록시가 삭제되었습니다.');
                loadProxies();
            },
            error: (xhr) => (window.AppUtils && AppUtils.showError(extractErrorDetail(xhr, '삭제에 실패했습니다.')))
        });
    }
}

// 그룹 관리
function loadGroups() {
    $.get('/api/proxy-groups')
        .done(groups => {
            const tbody = $('#groupTableBody');
            tbody.empty();
            
            groups.forEach(group => {
                tbody.append(`
                    <tr>
                        <td class="px-5 py-3 has-text-weight-semibold">${group.name}</td>
                        <td class="py-3 is-size-7 has-text-grey">${group.description || ''}</td>
                        <td class="has-text-centered py-3">
                            <span class="tag is-info is-light" style="font-weight:700;">${group.proxies_count}</span>
                        </td>
                        <td class="has-text-centered py-2" style="white-space: nowrap;">
                            <div class="buttons has-addons is-centered mb-0" style="flex-wrap: nowrap; justify-content: center;">
                                <button class="button is-subtle is-small" onclick="openModal('group', ${group.id})">수정</button>
                                <button class="button is-subtle-danger is-small" onclick="deleteGroup(${group.id})">삭제</button>
                            </div>
                        </td>
                    </tr>
                `);
            });
        })
        .fail((xhr) => {
            if (window.AppUtils) AppUtils.showError('그룹 목록을 불러오는데 실패했습니다.');
        });
}

function saveGroup() {
    const groupId = $('#groupId').val();
    const data = {
        name: $('#groupName').val(),
        description: $('#groupDescription').val() || null
    };

    const method = groupId ? 'PUT' : 'POST';
    const url = groupId ? `/api/proxy-groups/${groupId}` : '/api/proxy-groups';

    $.ajax({
        url,
        method,
        contentType: 'application/json',
        data: JSON.stringify(data),
        success: () => {
            if (window.AppToast) AppToast.success(groupId ? '그룹이 수정되었습니다.' : '그룹이 등록되었습니다.');
            loadGroups();
            loadProxies();
            closeModal('group');
        },
        error: (xhr) => (window.AppUtils && AppUtils.showError(extractErrorDetail(xhr, '저장에 실패했습니다.')))
    });
}

function deleteGroup(id) {
    if (confirm('정말 삭제하시겠습니까?\n그룹에 속한 프록시들은 기본 그룹으로 이동됩니다.')) {
        $.ajax({
            url: `/api/proxy-groups/${id}`,
            method: 'DELETE',
            success: () => {
                if (window.AppToast) AppToast.success('그룹이 삭제되었습니다.');
                loadGroups();
                loadProxies();
            },
            error: (xhr) => (window.AppUtils && AppUtils.showError(extractErrorDetail(xhr, '삭제에 실패했습니다.')))
        });
    }
}

// 일괄 등록 UI helpers
function openBulkProxyModal() {
    $('#bulkProxyInput').val('');
    $('#bulkProxyFile').val('');
    $('#bulkProxyFileName').text('선택된 파일 없음');
    resetBulkPreview();
    $('#bulkProxyModal').addClass('is-active');
}

function closeBulkProxyModal() {
    $('#bulkProxyModal').removeClass('is-active');
}

const PROXY_CSV_HEADER = "host,username,password,group_name,traffic_log_path,is_active,description,oids_json";

function toCsvField(value) {
    const str = String(value == null ? '' : value);
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
        return '"' + str.replace(/"/g, '""') + '"';
    }
    return str;
}

function downloadCsvFile(csv, filename) {
    const blob = new Blob(["\ufeff" + csv], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function downloadProxySampleCsv() {
    const oids = JSON.stringify({
        __interface_oids__: {
            eth0: { in_oid: "1.3.6.1.2.1.31.1.1.1.6.1", out_oid: "1.3.6.1.2.1.31.1.1.1.10.1" }
        }
    });
    const row = [
        "10.10.10.1", "admin", "mypassword123", "서울센터",
        "/var/log/mwg/traffic.log", "true", "Primary MWG", oids
    ].map(toCsvField).join(',');
    downloadCsvFile(PROXY_CSV_HEADER + "\n" + row + "\n", "pmt_proxy_sample.csv");
}

function exportProxiesToCsv() {
    $.get('/api/proxies', { limit: 500 })
        .done(proxies => {
            let csv = PROXY_CSV_HEADER + "\n";
            proxies.forEach(p => {
                const fields = [
                    p.host,
                    p.username || '',
                    '', // password is not exported for security
                    p.group_name || '',
                    p.traffic_log_path || '',
                    p.is_active ? 'true' : 'false',
                    p.description || '',
                    p.oids_json || ''
                ];
                csv += fields.map(toCsvField).join(',') + '\n';
            });
            downloadCsvFile(csv, `pmt_proxies_export_${new Date().toISOString().split('T')[0]}.csv`);
        })
        .fail(() => (window.AppUtils && AppUtils.showError('내보내기 실패')));
}

// 백엔드 schemas/proxy.py의 HostnameOrIPv4와 동일한 검증
const HOST_PATTERN = /^([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*|(?:\d{1,3}\.){3}\d{1,3})$/;

function splitCsvLine(raw) {
    const parts = [];
    let current = '';
    let inQuotes = false;
    for (let j = 0; j < raw.length; j++) {
        const char = raw[j];
        const nextChar = raw[j + 1];
        if (char === '"') {
            if (inQuotes && nextChar === '"') {
                current += '"';
                j++;
            } else {
                inQuotes = !inQuotes;
            }
        } else if (char === ',' && !inQuotes) {
            parts.push(current.trim());
            current = '';
        } else {
            current += char;
        }
    }
    parts.push(current.trim());
    return parts;
}

// 텍스트(콤마/탭 구분)를 행 단위로 파싱. 오류 행도 버리지 않고 반환.
// 반환: [{ lineNo, payload, errors: [] }]
function parseBulkText(text) {
    const lines = (text || '').replace(/^﻿/, '').split(/\r?\n/);
    const rows = [];
    const seenHosts = {};
    let headerChecked = false;

    for (let i = 0; i < lines.length; i++) {
        const raw = lines[i].trim();
        if (raw.length === 0) continue;

        // 탭 포함 시 엑셀 붙여넣기로 간주, 아니면 CSV 파싱
        const parts = raw.includes('\t')
            ? raw.split('\t').map(s => s.trim())
            : splitCsvLine(raw);

        // 첫 비어있지 않은 행의 첫 셀이 host면 헤더로 간주하고 스킵
        if (!headerChecked) {
            headerChecked = true;
            if ((parts[0] || '').toLowerCase() === 'host') continue;
        }

        const host = parts[0] || '';
        const username = parts[1] || '';
        const password = parts[2] || '';
        const groupNameRaw = parts[3] || '';
        const trafficLogPathRaw = parts[4] || '';
        const isActiveRaw = parts[5] || '';
        const descriptionRaw = parts[6] || '';
        const oidsJsonRaw = parts[7] || '';

        const errors = [];
        if (!host) errors.push('host 누락');
        else if (!HOST_PATTERN.test(host)) errors.push('host 형식 오류');
        if (!username) errors.push('username 누락');
        if (!password) errors.push('password 누락');

        const payload = { host, username, password };
        if (groupNameRaw) payload.group_name = groupNameRaw;
        if (trafficLogPathRaw) payload.traffic_log_path = trafficLogPathRaw;
        if (isActiveRaw) {
            const lowered = isActiveRaw.toLowerCase();
            if (['true', '1', 'yes', 'y', 'on'].includes(lowered)) payload.is_active = true;
            else if (['false', '0', 'no', 'n', 'off'].includes(lowered)) payload.is_active = false;
            else errors.push(`is_active 값 오류: ${isActiveRaw}`);
        }
        if (descriptionRaw) payload.description = descriptionRaw;
        if (oidsJsonRaw) {
            try {
                JSON.parse(oidsJsonRaw);
                payload.oids_json = oidsJsonRaw;
            } catch (e) {
                errors.push('oids_json이 유효한 JSON이 아님');
            }
        }

        const hostKey = host.toLowerCase();
        if (host && seenHosts[hostKey]) errors.push('입력 내 host 중복');
        if (host) seenHosts[hostKey] = true;

        rows.push({ lineNo: i + 1, payload, errors });
    }
    return rows;
}

// 일괄등록 미리보기 상태
let bulkRows = [];

function resetBulkPreview() {
    bulkRows = [];
    $('#bulkPreviewBody').empty();
    $('#bulkPreviewWrap').hide();
    $('#bulkPreviewSummary').hide();
    $('#bulkSubmitBtn').prop('disabled', true).text('등록');
}

function bulkStatusTag(row) {
    if (row.result) {
        if (row.result.status === 'created') return '<span class="tag is-success is-light">등록됨</span>';
        if (row.result.status === 'duplicate') return '<span class="tag is-warning is-light">중복</span>';
        return `<span class="tag is-danger is-light" title="${escapeHtml(row.result.detail || '')}">오류</span>`;
    }
    if (row.errors.length > 0) {
        return `<span class="tag is-danger is-light">${escapeHtml(row.errors.join(', '))}</span>`;
    }
    return '<span class="tag is-success is-light">준비됨</span>';
}

function escapeHtml(str) {
    return String(str == null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderBulkPreview() {
    const raw = $('#bulkProxyInput').val() || '';
    if (!raw.trim()) {
        resetBulkPreview();
        return;
    }
    bulkRows = parseBulkText(raw);

    const tbody = $('#bulkPreviewBody');
    tbody.empty();
    bulkRows.forEach(row => {
        const p = row.payload;
        const rowClass = row.errors.length > 0 ? ' style="background: var(--color-danger-bg, #feecf0);"' : '';
        const activeText = (p.is_active === false) ? '비활성' : '활성';
        tbody.append(`
            <tr${rowClass}>
                <td class="is-size-7 has-text-grey">${row.lineNo}</td>
                <td class="is-size-7">${escapeHtml(p.host)}</td>
                <td class="is-size-7">${escapeHtml(p.username)}</td>
                <td class="is-size-7">${escapeHtml(p.group_name || '')}</td>
                <td class="is-size-7 has-text-centered">${activeText}</td>
                <td class="is-size-7">${bulkStatusTag(row)}</td>
            </tr>
        `);
    });

    const validCount = bulkRows.filter(r => r.errors.length === 0).length;
    const errorCount = bulkRows.length - validCount;
    $('#bulkPreviewWrap').show();
    const summary = errorCount > 0
        ? `유효 ${validCount}건, 오류 ${errorCount}건 (오류 행은 등록에서 제외됩니다)`
        : `유효 ${validCount}건`;
    $('#bulkPreviewSummary').text(summary).show();
    $('#bulkSubmitBtn').prop('disabled', validCount === 0).text(validCount > 0 ? `${validCount}건 등록` : '등록');
}

function submitBulkProxies() {
    const validRows = bulkRows.filter(r => r.errors.length === 0);
    if (!validRows.length) {
        if (window.AppToast) AppToast.error('유효한 입력이 없습니다. host, username, password는 필수입니다.');
        return;
    }
    $('#bulkSubmitBtn').prop('disabled', true);
    $.ajax({
        url: '/api/proxies/bulk',
        method: 'POST',
        contentType: 'application/json',
        dataType: 'json',
        data: JSON.stringify(validRows.map(r => r.payload)),
        success: (results) => {
            const arr = Array.isArray(results) ? results : [];
            // 응답 index는 전송한 유효 행 기준 → 미리보기 행에 결과 매핑
            arr.forEach(r => {
                if (r && typeof r.index === 'number' && validRows[r.index]) {
                    validRows[r.index].result = r;
                }
            });

            const created = arr.filter(r => r && r.status === 'created').length;
            const dup = arr.filter(r => r && r.status === 'duplicate').length;
            const errors = arr.filter(r => r && r.status === 'error').length;

            if (window.AppToast) {
                const msg = `일괄 등록 완료: 생성 ${created}건, 중복 ${dup}건, 오류 ${errors}건`;
                if (errors > 0 || dup > 0) AppToast.warn(msg);
                else AppToast.success(msg);
            }

            loadProxies();
            if (created === arr.length && arr.length > 0) {
                // 전건 성공 시에만 자동 닫기
                closeBulkProxyModal();
            } else {
                // 실패/중복 행 상태를 미리보기에 표시
                renderBulkResultStatuses();
            }
        },
        error: (xhr) => {
            $('#bulkSubmitBtn').prop('disabled', false);
            if (window.AppToast) AppToast.error(extractErrorDetail(xhr, '일괄 등록 실패'));
        }
    });
}

function renderBulkResultStatuses() {
    const tbody = $('#bulkPreviewBody');
    tbody.empty();
    bulkRows.forEach(row => {
        const p = row.payload;
        const failed = row.errors.length > 0 || (row.result && row.result.status === 'error');
        const rowClass = failed ? ' style="background: var(--color-danger-bg, #feecf0);"' : '';
        const activeText = (p.is_active === false) ? '비활성' : '활성';
        tbody.append(`
            <tr${rowClass}>
                <td class="is-size-7 has-text-grey">${row.lineNo}</td>
                <td class="is-size-7">${escapeHtml(p.host)}</td>
                <td class="is-size-7">${escapeHtml(p.username)}</td>
                <td class="is-size-7">${escapeHtml(p.group_name || '')}</td>
                <td class="is-size-7 has-text-centered">${activeText}</td>
                <td class="is-size-7">${bulkStatusTag(row)}</td>
            </tr>
        `);
    });
    // 재전송 방지: 성공/중복 처리된 행 제외 후 재계산은 사용자가 입력을 수정하면 다시 이뤄짐
    $('#bulkSubmitBtn').prop('disabled', true).text('등록');
}

function extractErrorDetail(xhr, fallback) {
    try {
        const body = JSON.parse(xhr.responseText);
        if (body && body.detail) {
            return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
    } catch (e) {}
    return fallback;
}

function cloneProxy(id) {
    $.get(`/api/proxies/${id}`)
        .done(data => {
            // 새 프록시 등록 모달을 열되, 기존 데이터로 채움
            try { document.getElementById(`proxyForm`).reset(); } catch (e) {}
            $('#proxyId').val(''); // ID는 비움 (신규 등록)
            $('#host').val(''); // Host도 비움 (수동 입력 필요)
            $('#username').val(data.username);
            $('#password').val(''); // 비밀번호는 보안상 비움
            $('#traffic_log_path').val(data.traffic_log_path || '');
            $('#group_id').val(data.group_id || '');
            $('#description').val(`${data.host} 복제본`);
            $('#is_active').prop('checked', data.is_active);

            // OID 개별 설정 복제
            $('#proxyInterfaceList').empty();
            $('#use_custom_oids').prop('checked', false);
            $('#customOidSection').hide();

            if (data.oids_json) {
                try {
                    const oids = JSON.parse(data.oids_json);
                    if (oids && oids.__interface_oids__ && Object.keys(oids.__interface_oids__).length > 0) {
                        $('#use_custom_oids').prop('checked', true);
                        $('#customOidSection').show();
                        for (const [name, config] of Object.entries(oids.__interface_oids__)) {
                            const in_oid = typeof config === 'string' ? config : (config.in_oid || '');
                            const out_oid = typeof config === 'object' ? (config.out_oid || '') : '';
                            addProxyInterfaceRow(name, in_oid, out_oid);
                        }
                    }
                } catch (e) {}
            }
            $('#proxyModal').addClass('is-active');
        })
        .fail(() => (window.AppUtils && AppUtils.showError('데이터를 불러오는데 실패했습니다.')));
}

// 초기화
$(document).ready(() => {
    initProxyPage();
});

// PJAX 지원: 페이지 전환 후 초기화 재실행 (네임스페이스 사용하여 중복 등록 방지)
$(document).off('pjax:complete.proxy').on('pjax:complete.proxy', function(e, url) {
    if (url.includes('/proxy')) {
        initProxyPage();
    }
});

// expose for inline handlers
window.cloneProxy = cloneProxy;
window.switchProxySection = switchProxySection;
window.openBulkProxyModal = openBulkProxyModal;
window.closeBulkProxyModal = closeBulkProxyModal;
window.submitBulkProxies = submitBulkProxies;
window.openModal = openModal;
window.closeModal = closeModal;
window.saveProxy = saveProxy;
window.deleteProxy = deleteProxy;
window.saveGroup = saveGroup;
window.deleteGroup = deleteGroup;
window.toggleCustomOids = toggleCustomOids;
window.addProxyInterfaceRow = addProxyInterfaceRow;
window.downloadProxySampleCsv = downloadProxySampleCsv;
window.exportProxiesToCsv = exportProxiesToCsv;
