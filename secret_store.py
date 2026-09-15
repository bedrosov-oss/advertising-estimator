"""Explicit opt-in storage using only native credential backends."""
import re

SERVICES={'dadata','tavily'}
SERVICE='AdvertisingEstimator'


def backend():
    try:
        import keyring
        value=keyring.get_keyring()
    except Exception:raise ValueError('Системное хранилище ключей недоступно. Ключ можно ввести на странице поиска.') from None
    if type(value).__module__ not in ('keyring.backends.macOS','keyring.backends.Windows','keyring.backends.SecretService'):
        raise ValueError('Нет поддерживаемого системного хранилища. Установите keyring через SETUP_FEATURES; текстовые файлы для ключей не используются.')
    return value


def service(name):
    if name not in SERVICES:raise ValueError('Выберите DaData или Tavily.')
    return name


def status():
    try:
        vault=backend()
        return {'available':True,'stored':{name:bool(vault.get_password(SERVICE,name)) for name in sorted(SERVICES)}}
    except Exception as exc:
        return {'available':False,'stored':{},'message':str(exc) if isinstance(exc,ValueError) else 'Системное хранилище не предоставило доступ.'}


def save(name,key):
    name=service(name)
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,256}',key):raise ValueError('Неверный формат API-ключа.')
    try:backend().set_password(SERVICE,name,key)
    except ValueError:raise
    except Exception:raise ValueError('Не удалось сохранить ключ в системном хранилище.') from None
    return {'saved':True,'service':name}


def remove(name):
    name=service(name)
    try:
        vault=backend()
        if vault.get_password(SERVICE,name):vault.delete_password(SERVICE,name)
    except ValueError:raise
    except Exception:raise ValueError('Не удалось удалить ключ из системного хранилища.') from None
    return {'deleted':True,'service':name}


def effective(body,name):
    key=body.get('api_key')
    if key:return key
    if body.get('use_saved_key') is not True:return key
    try:key=backend().get_password(SERVICE,service(name))
    except ValueError:raise
    except Exception:raise ValueError('Системное хранилище не предоставило доступ к ключу.') from None
    if not key:raise ValueError('Сохранённого ключа нет. Добавьте его во вкладке «Документы и склад» или введите на странице поиска.')
    return key
