import re

import django
from django import template
from django.conf import settings
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.templatetags import admin_list
from django.contrib.admin.util import unquote
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction, models
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render_to_response
from django.utils import dateformat, simplejson
from django.utils.html import escape, conditional_escape
from django.utils.encoding import force_unicode, smart_str, smart_unicode
from django.utils.functional import update_wrapper
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
from django.utils.translation import get_date_formats, get_partial_date_formats, ugettext as _

EDITOR_ADMIN_MEDIA = getattr(settings, 'EDITOR_ADMIN_MEDIA', '/media/editor/')
FRONTEND_EDITING_MATCHER = re.compile(r'(\d+)/(\w+)/(\d+)')

class TreeEditorMixin(object):
    actions = None # TreeEditorMixin does not like the checkbox column

    def changelist_view(self, request, extra_context=None):
        # handle AJAX requests
        if request.is_ajax():
            cmd = request.POST.get('__cmd')
            if cmd=='save_tree':
                return self._save_tree(request)
            elif cmd=='delete_item':
                return self._delete_item(request)

            return HttpResponse('Oops. AJAX request not understood.')

        from django.contrib.admin.views.main import ChangeList, ERROR_FLAG
        opts = self.model._meta
        app_label = opts.app_label

        if not self.has_change_permission(request, None):
            raise PermissionDenied
        try:
            if django.VERSION[0] < 1 or (django.VERSION[0] == 1 and django.VERSION[1] < 1):
                self.changelist = ChangeList(request, self.model, self.list_display,
                    self.list_display_links, self.list_filter, self.date_hierarchy,
                    self.search_fields, self.list_select_related, self.list_per_page,
                    self)
            else:
                self.changelist = ChangeList(request, self.model, self.list_display,
                    self.list_display_links, self.list_filter, self.date_hierarchy,
                    self.search_fields, self.list_select_related, self.list_per_page,
                    self.list_editable, self)
        except IncorrectLookupParameters:
            # Wacky lookup parameters were given, so redirect to the main
            # changelist page, without parameters, and pass an 'invalid=1'
            # parameter via the query string. If wacky parameters were given and
            # the 'invalid=1' parameter was already in the query string, something
            # is screwed up with the database, so display an error page.
            if ERROR_FLAG in request.GET.keys():
                return render_to_response('admin/invalid_setup.html', {'title': _('Database error')})
            return HttpResponseRedirect(request.path + '?' + ERROR_FLAG + '=1')

        context = {
            'EDITOR_ADMIN_MEDIA': EDITOR_ADMIN_MEDIA,
            'title': self.changelist.title,
            'is_popup': self.changelist.is_popup,
            'cl': self.changelist,
            'has_add_permission': self.has_add_permission(request),
            'root_path': self.admin_site.root_path,
            'app_label': app_label,
            'object_list': self.model._tree_manager.all(),
            'tree_editor': self,

            'result_headers': list(admin_list.result_headers(self.changelist)),
        }
        context.update(extra_context or {})
        return render_to_response([
            'admin/%s/%s/tree_editor.html' % (app_label, opts.object_name.lower()),
            'admin/%s/tree_editor.html' % app_label,
            'admin/editor/tree_editor.html',
            ], context, context_instance=template.RequestContext(request))

    def object_list(self):
        for item in self.model._tree_manager.all().select_related():
            yield item, unicode(item), _properties(self.changelist, item)

    def _save_tree(self, request):
        itemtree = simplejson.loads(request.POST['tree'])
        # 0 = tree_id, 1 = parent_id, 2 = left, 3 = right, 4 = level, 5 = item_id
        sql = "UPDATE %s SET %s=%%s, %s_id=%%s, %s=%%s, %s=%%s, %s=%%s WHERE %s=%%s" % (
            self.model._meta.db_table,
            self.model._meta.tree_id_attr,
            self.model._meta.parent_attr,
            self.model._meta.left_attr,
            self.model._meta.right_attr,
            self.model._meta.level_attr,
            self.model._meta.pk.column)

        connection.cursor().executemany(sql, itemtree)
        transaction.commit_unless_managed()

        return HttpResponse("OK", mimetype="text/plain")

    def _delete_item(self, request):
        item_id = request.POST['item_id']
        obj = self.model._default_manager.get(pk=unquote(item_id))
        obj.delete()
        return HttpResponse("OK", mimetype="text/plain")



# copied from django.contrib.admin.templatetags.admin_list and slightly modified
def _boolean_icon(field_val):
    BOOLEAN_MAPPING = {True: 'yes', False: 'no', None: 'unknown'}
    return mark_safe(u'<img src="%simg/admin/icon-%s.gif" alt="%s" />' % (settings.ADMIN_MEDIA_PREFIX, BOOLEAN_MAPPING[field_val], field_val))


def _properties(cl, result):
    first = True
    pk = cl.lookup_opts.pk.attname
    EMPTY_CHANGELIST_VALUE = '(None)'

    for field_name in cl.list_display[1:]:
        try:
            f = cl.lookup_opts.get_field(field_name)
        except models.FieldDoesNotExist:
            try:
                if callable(field_name):
                    attr = field_name
                    value = attr(result)
                elif hasattr(cl.model_admin, field_name) and \
                   not field_name == '__str__' and not field_name == '__unicode__':
                    attr = getattr(cl.model_admin, field_name)
                    value = attr(result)
                else:
                    attr = getattr(result, field_name)
                    if callable(attr):
                        value = attr()
                    else:
                        value = attr
                allow_tags = getattr(attr, 'allow_tags', False)
                boolean = getattr(attr, 'boolean', False)
                if boolean:
                    allow_tags = True
                    result_repr = _boolean_icon(value)
                else:
                    result_repr = smart_unicode(value)
            except (AttributeError, models.ObjectDoesNotExist):
                result_repr = EMPTY_CHANGELIST_VALUE
            else:
                # Strip HTML tags in the resulting text, except if the
                # function has an "allow_tags" attribute set to True.
                if not allow_tags:
                    result_repr = escape(result_repr)
                else:
                    result_repr = mark_safe(result_repr)
        else:
            field_val = getattr(result, f.attname)

            if isinstance(f.rel, models.ManyToOneRel):
                if field_val is not None:
                    result_repr = escape(getattr(result, f.name))
                else:
                    result_repr = EMPTY_CHANGELIST_VALUE
            # Dates and times are special: They're formatted in a certain way.
            elif isinstance(f, models.DateField) or isinstance(f, models.TimeField):
                if field_val:
                    (date_format, datetime_format, time_format) = get_date_formats()
                    if isinstance(f, models.DateTimeField):
                        result_repr = capfirst(dateformat.format(field_val, datetime_format))
                    elif isinstance(f, models.TimeField):
                        result_repr = capfirst(dateformat.time_format(field_val, time_format))
                    else:
                        result_repr = capfirst(dateformat.format(field_val, date_format))
                else:
                    result_repr = EMPTY_CHANGELIST_VALUE
            # Booleans are special: We use images.
            elif isinstance(f, models.BooleanField) or isinstance(f, models.NullBooleanField):
                result_repr = _boolean_icon(field_val)
            # DecimalFields are special: Zero-pad the decimals.
            elif isinstance(f, models.DecimalField):
                if field_val is not None:
                    result_repr = ('%%.%sf' % f.decimal_places) % field_val
                else:
                    result_repr = EMPTY_CHANGELIST_VALUE
            # Fields with choices are special: Use the representation
            # of the choice.
            elif f.flatchoices:
                result_repr = dict(f.flatchoices).get(field_val, EMPTY_CHANGELIST_VALUE)
            else:
                result_repr = escape(field_val)
        if force_unicode(result_repr) == '':
            result_repr = mark_safe('&nbsp;')
        # If list_display_links not defined, add the link tag to the first field
        if (first and not cl.list_display_links) or field_name in cl.list_display_links:
            table_tag = {True:'th', False:'td'}[first]
            first = False
            url = cl.url_for_result(result)
            # Convert the pk to something that can be used in Javascript.
            # Problem cases are long ints (23L) and non-ASCII strings.
            if cl.to_field:
                attr = str(cl.to_field)
            else:
                attr = pk
            value = result.serializable_value(attr)
            result_id = repr(force_unicode(value))[1:]
            yield mark_safe(u'<%s><a href="%s"%s>%s</a></%s>' % \
                (table_tag, url, (cl.is_popup and ' onclick="opener.dismissRelatedLookupPopup(window, %s); return false;"' % result_id or ''), conditional_escape(result_repr), table_tag))
        else:
            # By default the fields come from ModelAdmin.list_editable, but if we pull
            # the fields out of the form instead of list_editable custom admins
            # can provide fields on a per request basis
            result_repr = conditional_escape(result_repr)
            yield mark_safe(u'<td>%s</td>' % (result_repr))

        first = False
