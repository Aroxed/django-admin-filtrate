import json

from django.utils.encoding import force_str
from django.contrib.admin.filters import SimpleListFilter
from django.utils.safestring import mark_safe
from django.utils.datastructures import MultiValueDictKeyError
from django.utils.translation import gettext_lazy as _
from django.forms.widgets import Media, MEDIA_TYPES, Input, HiddenInput
from django import forms as f

from filtrate import settings


class FiltrateFilter(SimpleListFilter):
    """
    The base django_admin_filtrate filter. It requires overriding of `title`,
    `parameter_name`, `queryset()` and `get_content()`.

    If your are using a form, adding `_form_duplicate_getparams()` inside the
    form html tags might come in handy.
    """
    template = 'filtrate/filter.html'
    title = 'Efter afdeling'

    def __init__(self, request, params, model, model_admin):
        self._add_media(model_admin)
        self.request = request
        self.params = params
        self.model = model
        self.model_admin = model_admin
        super(FiltrateFilter, self).__init__(request, params, model,
                                             model_admin)

    class Media:
        js = ('filtrate/js/filtrate.js', )
        css = {'all': ('filtrate/css/filtrate.css', )}

    def _add_media(self, model_admin):
        def _get_media(obj):
            return Media(media=getattr(obj, 'Media', None))

        media = (_get_media(model_admin) + _get_media(FiltrateFilter) +
                 _get_media(self))

        for name in MEDIA_TYPES:
            setattr(model_admin.Media, name, getattr(media, "_" + name))

    def _form_duplicate_getparams(self, omitted_fields):
        """Replicates the get parameters as hidden form fields."""
        s = '<input type="hidden" name="%s" value="%s"/>'
        _omitted_fields = tuple(omitted_fields) + ('e', )
        _keys = list(self.request.GET.keys())
        return "".join([
            s % (k, self.request.GET[k]) for k in _keys
            if k not in _omitted_fields
        ])

    def lookups(self, request, model_admin):
        """
        Fake lookups.
        """
        return [('_', '_')]

    def choices(self, cl):
        """
        Wrap title and content as the first choice.
        """
        return [{
            'title': self.get_title(),
            'content': self.get_content(self.request),
        }]

    def get_title(self, request):
        """
        Change the title dynamically.
        """
        return self.title

    def get_content(self, request):
        """
        The content part of the filter in html.
        """
        raise NotImplementedError()

    def queryset(self, request, queryset):
        """
        Filter the queryset based on the request.
        """
        value = self.value()
        if value is None:
            return queryset
        else:
            return queryset.filter(**{self.parameter_name: value})


class DateRangeFilter(FiltrateFilter):
    class Media:
        js = ('filtrate/js/daterangefilter.js', )

        if settings.FILTRATE['include_jquery']:
            js = (
                '//ajax.googleapis.com/ajax/libs/jqueryui/1.8.14/jquery-ui.min.js',
                '//ajax.googleapis.com/ajax/libs/jqueryui/1.8.14/i18n/jquery-ui-i18n.min.js',
            ) + js
            css = {
                'all':
                ('//ajax.googleapis.com/ajax/libs/jqueryui/1.8.14/themes/flick/jquery-ui.css',
                 )
            }

    def _get_form(self, field_name):
        """
        Returns form with from and to fields. The '__alt' fields are alternative
        fields with the correct non localized dateform needed for Django,
        handled by jsTree.
        """
        from_name = self.parameter_name + '__gte'
        to_name = self.parameter_name + '__lte'

        display_widget = Input(attrs={'class': 'filtrate_date'})
        hidden_widget = HiddenInput(attrs={'class': 'filtrate_date_hidden'})

        def add_fields(fields, name, label):
            fields[name + '__alt'] = f.CharField(label=label,
                                                 widget=display_widget,
                                                 required=False)
            fields[name] = f.CharField(widget=hidden_widget, required=False)

        def add_data(data, name, request):
            date = request.GET.get(name)

            if date:
                data[name + '__alt'] = date

        class DateRangeForm(f.Form):
            def __init__(self, *args, **kwargs):
                super(DateRangeForm, self).__init__(*args, **kwargs)
                add_fields(self.fields, from_name, _('From'))
                add_fields(self.fields, to_name, _('To'))

        data = {}
        add_data(data, from_name, self.request)
        add_data(data, to_name, self.request)
        return DateRangeForm(data=data)

    def get_content(self, request):
        form = self._get_form(self.parameter_name)
        return mark_safe("""
            <script>
                var filtrate = filtrate || {};
                filtrate.datepicker_region = '%(datepicker_region)s';
                filtrate.datepicker_date_format = '%(datepicker_date_format)s';
            </script>
            <form class="filtrate_daterange_form" method="get">
                %(form)s
            <input type="submit" value="%(submit)s" />
            %(get_params)s
            </form>
        """ % ({
            'form':
            form.as_p(),
            'submit':
            _('Apply filter'),
            'datepicker_region':
            settings.FILTRATE['datepicker_region'],
            'datepicker_date_format':
            settings.FILTRATE['datepicker_date_format'],
            'get_params':
            self._form_duplicate_getparams(list(form.fields.keys())),
        }))


class TreeFilter(FiltrateFilter):
    """
    A tree filter for models. Uses the jsTree jQuery plugin found at
    http://www.jstree.com/ in the frontend.

    Overiding classes needs to implement `parameter_name`, `title`, and
    `get_tree()`.
    """

    INCLUDE = 1  # The selected nodes are included in the query with ".filter()".
    EXCLUDE = 2  # The selected nodes are excluded in the query with ".exclude()".
    query_mode = INCLUDE

    # The keyword argument used in the Django ORM query. If None it defaults to
    # the parameter_name.
    query_name = None

    def __init__(self, request, params, model, model_admin):
        super(TreeFilter, self).__init__(request, params, model, model_admin)
        if self.value() is not None:
            self.selected_nodes = list(map(int, self.value().split(',')))
        else:
            self.selected_nodes = []

    class Media:
        js = (
            'filtrate/js/filtertree.js',
        )

    def _tree_to_json(self, tree):
        """Recusively walks through the tree and generate json in a format
        suitable for jsTree."""
        def parse_tree(tree, cur_tree):
            for node in tree:
                if type(node) == type(tuple()):
                    # Is a parent node.
                    title = force_str(node[0])
                    new_tree = []
                    cur_tree.append({
                        'data': title,
                        'children': new_tree,
                    })
                    parse_tree(node[1], new_tree)
                else:
                    # Is a leaf node.
                    title = force_str(node)
                    cur_tree.append({
                        "attr": {
                            "obj_id": node.pk,
                            "is_selected": node.pk in self.selected_nodes,
                        },
                        'data': force_str(node),
                    })

        json_tree = []
        parse_tree(tree, json_tree)
        return json.dumps(json_tree)

    def get_content(self, request):
        """Return html for entire filter."""
        return mark_safe("""
            <div class="treefilter">
                <textarea style="display: none;">%s</textarea>
                <form action_method="get">
                    <div class="tree"></div>
                    <input class="checked" type="hidden" name="%s" value=""/>
                    <input type="submit" value="Send" />
                    %s
                </form>
            </div>
        """ % (self._tree_to_json(self.get_tree()), self.parameter_name,
               self._form_duplicate_getparams((self.parameter_name, ))))

    def get_tree(self):
        """
        Must return a tree of model instances as a tuple of objects or tuples
        as:
        ( # Root level.
            obj1,
            obj2,
            ( # Nested level.
                obj3,
                obj4,
                ( # More nested level.
                    obj3,
                    obj4,
                ),
            ),
        )
        """
        raise NotImplementedError

    def queryset(self, request, queryset):
        if not self.selected_nodes:
            return queryset
        else:
            query_name = self.parameter_name if self.query_name is None else self.query_name
            kwargs = {query_name: self.selected_nodes}
            if self.query_mode == self.INCLUDE:
                return queryset.filter(**kwargs)
            elif self.query_mode == self.EXCLUDE:
                return queryset.exclude(**kwargs)
            else:
                raise Exception("Unknown query_mode given.")
