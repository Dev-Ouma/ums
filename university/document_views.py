"""Presentation helpers used only after the originating view authorizes a report."""
from django.shortcuts import render


def present_pdf(request, response, title='Document preview'):
    query = request.GET.copy()
    query.pop('preview', None)
    query.pop('download', None)
    query['inline'] = '1'
    pdf_url = request.path + '?' + query.urlencode()
    if request.GET.get('preview') == '1':
        query.pop('inline', None)
        query['download'] = '1'
        return render(request, 'dashboard/pdf_viewer.html', {
            'title': title, 'pdf_url': pdf_url,
            'download_url': request.path + '?' + query.urlencode(),
            'icon': 'fa-file-pdf',
        })
    if request.GET.get('download') == '1':
        response['Content-Disposition'] = response.get('Content-Disposition', 'attachment').replace('inline;', 'attachment;', 1)
    elif request.GET.get('inline') == '1':
        response['Content-Disposition'] = response.get('Content-Disposition', 'inline').replace('attachment;', 'inline;', 1)
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    # The global CSP correctly blocks framing by default. Mark only this
    # already-authorized PDF response so the same-origin document viewer can
    # embed it without weakening CSP for normal application pages.
    response._ums_allow_same_origin_frame = True
    return response
