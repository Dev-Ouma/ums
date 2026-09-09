from .control_services import permitted, current_status, active_messages, route_scope

def control_context(request):
    user = getattr(request, 'user', None)
    match = getattr(request, 'resolver_match', None)
    scope = route_scope(request.path_info, getattr(match, 'url_name', ''), getattr(match, 'namespace', ''))
    location = 'LOGIN' if getattr(match, 'url_name', '') == 'login' else 'DASHBOARD'
    notices = active_messages(user, module_ids=scope[0])
    banners = [n for n in notices if ('BANNER' in n.locations or location in n.locations or getattr(user, 'role', None) in n.locations or ('MODULE' in n.locations and n.modules.exists()))]
    is_admin = getattr(user, 'is_superuser', False) or getattr(user, 'role', '') == 'ADMIN'
    return {
        'system_state': current_status(),
        'system_banners': banners,
        'system_sidebar_messages': [n for n in notices if 'SIDEBAR' in n.locations],
        'can_control_maintenance': is_admin or any(permitted(user, p) for p in ['maintenance.view', 'maintenance.deactivate']),
        'can_control_lockdown': is_admin or any(permitted(user, p) for p in ['lockdown.view', 'lockdown.deactivate']),
        'can_control_messages': is_admin or permitted(user, 'messages.view'),
        'can_control_health': is_admin or permitted(user, 'health.view'),
    }
