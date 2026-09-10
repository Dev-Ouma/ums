from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django import forms
from django.conf import settings
from django.utils import timezone
from .models import SystemRestriction, Notice, MessageTemplate
from .control_services import validate_fields

LOCATIONS=[(v,v.replace('_',' ').title()) for v in ['LOGIN','DASHBOARD','SIDEBAR','IN_APP','BANNER','MODULE','STUDENT','FACULTY','ADMIN','EMAIL']]

class StyledForm(forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():
            field.widget.attrs['class']='form-check-input' if isinstance(field.widget,forms.CheckboxInput) else 'form-select' if isinstance(field.widget,forms.Select) else 'form-control'
            if isinstance(field.widget,forms.Textarea): field.widget.attrs['rows']=3

class RestrictionForm(StyledForm):
    roles=forms.MultipleChoiceField(choices=[('PUBLIC','Public visitors'),('STUDENT','Students'),('FACULTY','Faculty'),('ADMIN','Administrators')],required=False)
    warning_minutes=forms.MultipleChoiceField(choices=[('30','30 minutes'),('10','10 minutes'),('5','5 minutes')],required=False)
    starts_at=forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    ends_at=forms.DateTimeField(required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    class Meta:
        model=SystemRestriction
        fields=['title','description','kind','reason','public_message','starts_at','ends_at','time_zone','modules','submodules','features','roles','allow_bypass','session_policy','warning_minutes','notification_message']
    def clean_warning_minutes(self):
        return [int(x) for x in self.cleaned_data['warning_minutes']]
    def clean(self):
        data=super().clean()
        try:
            zone=ZoneInfo(data.get('time_zone',''))
        except (ZoneInfoNotFoundError,ValueError):
            self.add_error('time_zone','Enter a valid IANA time zone, such as Africa/Nairobi.'); return data
        # HTML local values are interpreted in the explicitly selected zone.
        from datetime import datetime
        for key in ['starts_at','ends_at']:
            raw=self.data.get(key)
            if raw:
                try:
                    dt=datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        aware=dt.replace(tzinfo=zone)
                        if aware.astimezone(ZoneInfo('UTC')).astimezone(zone).replace(tzinfo=None)!=dt or dt.replace(tzinfo=zone,fold=0).utcoffset()!=dt.replace(tzinfo=zone,fold=1).utcoffset():
                            self.add_error(key,'Ambiguous or nonexistent local time; choose another time.'); continue
                        data[key]=aware
                except ValueError:
                    pass
        if data.get('starts_at') and data.get('ends_at') and data['ends_at']>=data['starts_at']:
            pass
        elif data.get('ends_at'):
            self.add_error('ends_at','End must follow start.')
        scoped=any(data.get(k) for k in ['modules','submodules','features'])
        if data.get('kind')=='MODULE' and not scoped:
            self.add_error('modules','Select at least one module, submodule or feature.')
        if data.get('kind')=='ROLE' and not data.get('roles'):
            self.add_error('roles','Select at least one user category.')
        if data.get('kind') in {'LOCKDOWN','EMERGENCY'} and (scoped or data.get('roles')):
            self.add_error('kind','Full and emergency lockdown must cover the entire system. Use module or role lockdown for a partial restriction.')
        for key in ['modules','submodules']:
            if data.get(key) is not None and data[key].filter(is_critical=True).exists():
                self.add_error(key,'Protected core modules cannot be restricted.')
        if scoped and data.get('session_policy')!='BLOCK':
            self.add_error('session_policy','Scoped incidents must block requests so unrelated module sessions remain usable.')
        for key in ['public_message','notification_message']:
            try: validate_fields(data.get(key,''))
            except forms.ValidationError as error: self.add_error(key,error)
        return data

class MessageForm(StyledForm):
    locations=forms.MultipleChoiceField(choices=LOCATIONS,required=False)
    class Meta:
        model=Notice
        fields=['title','body','message_type','priority','audience','recipients','target_roles','departments','programmes','modules','starts_at','ends_at','locations','banner_mode','animation_enabled','animation_speed','animation_direction','dismissible','persistent','action_url','action_label','display_order','template']
        widgets={'starts_at':forms.DateTimeInput(attrs={'type':'datetime-local'}),'ends_at':forms.DateTimeInput(attrs={'type':'datetime-local'})}
    def clean(self):
        data=super().clean()
        if data.get('ends_at') and data.get('starts_at') and data['ends_at']<=data['starts_at']:
            self.add_error('ends_at','End must follow start.')
        if data.get('animation_speed') and data['animation_speed'] < 10:
            self.add_error('animation_speed','Use at least 10 seconds so ticker messages remain readable.')
        if data.get('action_url') and data['action_url'].startswith(('javascript:', 'data:')):
            self.add_error('action_url','Enter a normal application URL or path.')
        if 'EMAIL' in data.get('locations',[]) and not getattr(settings,'SYSTEM_CONTROL_EMAIL_ENABLED',False):
            self.add_error('locations','Email delivery is not configured. Select in-app delivery.')
        for key in ['title','body']:
            try: validate_fields(data.get(key,''))
            except forms.ValidationError as error: self.add_error(key,error)
        return data

class TemplateForm(StyledForm):
    class Meta:
        model=MessageTemplate
        fields=['name','title','body']
    def clean(self):
        data=super().clean()
        for key in ['title','body']:
            try: validate_fields(data.get(key,''))
            except forms.ValidationError as error: self.add_error(key,error)
        return data
