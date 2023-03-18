import logging
import requests
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType, Event
from vk_api.keyboard import VkKeyboard as BaseVkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id


from database import create_seen_users_table, insert_seen_user_data, select_seen_user


user_access_token = ''
group_token = ''


logging.basicConfig(level=logging.INFO)


class VkKeyboard(BaseVkKeyboard):
    def add_button(self, label, color=VkKeyboardColor.SECONDARY, payload=None):
        """Добавляем текучий интерфейс."""
        super().add_button(label, color, payload)
        return self


class ArtificialEvent:
    def __init__(self, user_id, text):
        self.user_id = user_id
        self.text = text
        self.to_me = True
        self.type = VkEventType.MESSAGE_NEW


class StateMachine:
    def __init__(self, states: dict, initial_state: str):
        self.states = states
        self.current_state = initial_state
        self.context_data = {}

    def transition(self, command=None, next_context=None):
        if command is not None:
            next_state_allowed = self.states[self.current_state].get('commands').get(command)

            if next_state_allowed is not None:
                self.set_current_state(next_state_allowed)
        else:
            self.set_current_state(next_context)

        if self.current_state == 'goodbuy':
            self.set_current_state('root')

        return self.current_state

    def set_current_state(self, new_state):
        self.current_state = new_state

    def get_pre_message_hooks(self):
        return self.states[self.current_state].get('pre_hooks')

    def get_post_message_hooks(self):
        return self.states[self.current_state].get('post_hooks')

    def get_message(self):
        return self.states[self.current_state]['message']

    def get_keyboard(self):
        if keyb := self.states[self.current_state].get('keyboard'):
            return keyb.get_keyboard()
        return None


def set_partner_gender(bot, user_id, partner_gender):
    bot.storage[user_id]['partner_gender'] = partner_gender
    return 'start_search'

# Определим состояния системы
states = {
    'root': {
        'message': """Приветствую! Я - бот VKinder, который может помочь тебе найти пару. Я отправлю тебе три самые популярные  
фотографии пользователя, чтобы ты мог составить своё первое впечатление.""",
        'keyboard': VkKeyboard(inline=True)
        .add_button('Начать поиск', color=VkKeyboardColor.POSITIVE)
        .add_button('Закончить разговор', color=VkKeyboardColor.SECONDARY),
        'commands': {
            'Начать поиск': 'start_search',
            'Закончить разговор': 'goodbuy',
        }
    },
    'goodbuy': {
        'message': 'Goodbuy!',
    },
    'start_search': {
        'message': 'Отлично, тогда начинаю поиск. Это займет некоторое время.',
        'commands': {},
        'pre_hooks': [
            create_seen_users_table,
        ],
        'post_hooks': [
            'get_suggested_candidates',
        ],
    },
    'search_in_progress': {
        'message': '',
        'keyboard': VkKeyboard(inline=True)
            .add_button('Посмотреть анкеты', color=VkKeyboardColor.POSITIVE)
            .add_button('Back', color=VkKeyboardColor.SECONDARY),
        'commands': {
            'Посмотреть анкеты': 'search_in_progress',
            'Back': 'root'
        },
        'post_hooks': [
            'view_next_profile',
        ]
    },

    'show_results_context': {
        'message': 'Поиск окончен. Нажмите кнопку "Посмотреть анкеты".',
        'keyboard': VkKeyboard(inline=True)
            .add_button('Посмотреть анкеты', color=VkKeyboardColor.POSITIVE)
            .add_button('Закончить поиск', color=VkKeyboardColor.SECONDARY),
        'commands': {
            'Посмотреть анкеты': 'view_profile',
            'Закончить поиск': 'goodbuy',
        }
    },
    'view_profile': {
        'message': 'Вот он профиль!!!!',  # Фактическое сообщение будет задано позже
        'keyboard': VkKeyboard(inline=True)
            .add_button('Следующий профиль', color=VkKeyboardColor.POSITIVE)
            .add_button('Закончить поиск', color=VkKeyboardColor.SECONDARY),
        'commands': {
            'Следующий профиль': 'view_profile',
            'Закончить поиск': 'goodbuy',
        },
    },
}


def get_popular_photos(user_id):
    user_vk_session = vk_api.VkApi(token=user_access_token)
    try:
        photos = user_vk_session.method('photos.get', {
            'user_id': user_id,
            'album_id': 'profile',
            'extended': 1,
            'count': 30,
            'v': '5.131'
        })
    except vk_api.VkApiError as e:
        return {'owner_id': None, 'pics_ids': []}

    if not photos['items']:
        return {'owner_id': None, 'pics_ids': []}

    popular_photos = sorted(
        photos['items'],
        key=lambda k: k['likes']['count'] + k['comments']['count'],
        reverse=True
    )[:3]

    return {
        'owner_id': popular_photos[0]['owner_id'],
        'pics_ids': [photo['id'] for photo in popular_photos]
    }


class VKBotSearch:
    def __init__(self):
        self.vk_session = vk_api.VkApi(token=group_token)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkLongPoll(self.vk_session)
        self.state_machine = StateMachine(states, 'root')
        self.state_machine.context_data = {'profiles_to_send': []}
        self.user_info_cache = {}
        self.log = logging.getLogger()
        self.log.info('Бот запущен')
        self.storage = {}

    def send_message(self, user_id, text=None, attachments=None):
        text_msg = text or self.state_machine.get_message()
        if not text_msg:
            text_msg = '...'
        self.vk.messages.send(
            user_id=user_id,
            message=text_msg,
            keyboard=self.state_machine.get_keyboard(),
            attachment=','.join(attachments) if attachments else None,
            random_id=get_random_id()
        )

    def get_first_name(self, user_id):
        first_name = self.get_user_info(user_id)['first_name']
        return first_name

    def get_user_info(self, user_id):
        if user_id not in self.user_info_cache:
            user_info = self.vk.method('users.get', {'user_ids': user_id})
            self.user_info_cache[user_id] = user_info[0] if user_info else None
            self.log.info(f'Информация о пользователе {user_id} помещена в кэш')
        self.log.info(f'Обращение к кэшу об информации пользователе {user_id}')
        return self.user_info_cache[user_id]

    def get_age(self, user_id):
        return '25', '35'

    def get_sex(self, user_id):
        return "1"

    def find_city(self, user_id):
        return "Красноярск"

    def find_user_params(self, user_id):  # оьедени с search_users
        """
        Сбор параметров для авто поиска
        """
        # fields = 'id, sex, bdate, city, relation'
        age_from, age_to = self.get_age(user_id)
        params = {'access_token': user_access_token,
                  'v': '5.131',
                  'sex': self.get_sex(user_id),
                  'age_from': age_from,
                  'age_to': age_to,
                  'country_id': '1',
                  'hometown': self.find_city(user_id),
                  'fields': 'id, sex, bdate, city, relation',
                  'status': '1' or '6',
                  'count': 20,
                  'has_photo': '1',
                  'is_closed': False
                  }
        return params

    def search_users(self, user_id):
        """
        Поиск юзеров по полученным данным для авто поиска
        """
        all_persons = []
        url = f'https://api.vk.com/method/users.search'
        res = requests.get(url, self.find_user_params(user_id)).json()
        user_url = f'https://vk.com/id'

        for element in res['response'].get('items'):
            profile_pics = get_popular_photos(element['id'])
            if profile_pics:
                attach = ''
                for pic in profile_pics['pics_ids']:
                    self.log.debug(f"---Фотография для профиля: {pic}")
                    attach += f'photo{profile_pics["owner_id"]}_{pic},'

                self.log.info(f"Фотографии для профиля {element['id']}: {attach}")
                person = [
                    element['first_name'],
                    element['last_name'],
                    user_url + str(element['id']),
                    element['id'],
                    attach
                ]
                all_persons.append(person)
        return all_persons

    def filter_unseen_users(self, profiles, user_id):
        profiles_to_send = []
        while len(profiles) > 0:
            profile = profiles.pop()
            if select_seen_user(user_id, profile[3]) is None:  # проверяем нет ли повторений
                profiles_to_send.append(profile)
        return profiles_to_send

    def get_suggested_candidates(self, user_id):
        profiles = self.search_users(user_id)
        self.log.info(f'Количество найденных профилей для пользователя #{user_id}: {len(profiles)}')
        self.state_machine.context_data['profiles_to_send'] = self.filter_unseen_users(profiles, user_id)

        if not self.state_machine.context_data['profiles_to_send']:
            self.send_message(
                user_id,
                text='Нет доступных профилей',
            )
            self.state_machine.transition('root')
            return
        else:
            self.state_machine.transition(next_context='show_results_context')
            self.send_artificial_event(user_id, '')

    def _run_hooks(self, user_id, hooks=None):
        if hooks:
            for hook in hooks:
                if callable(hook):
                    hook()
                elif isinstance(hook, str):
                    try:
                        hook_method = getattr(self, hook)
                    except AttributeError:
                        pass
                    else:
                        hook_method(user_id)

    def run_pre_hooks(self, event):
        user_id = str(event.user_id)
        hooks = self.state_machine.get_pre_message_hooks()
        self.log.debug('pre hooks to run:', hooks)
        self._run_hooks(user_id, hooks)

    def run_post_hooks(self, event):
        user_id = str(event.user_id)
        hooks = self.state_machine.get_post_message_hooks()
        self.log.debug('post hooks to run:', hooks)
        self._run_hooks(user_id, hooks)

    def view_next_profile(self, user_id):
        if not self.state_machine.context_data['profiles_to_send']:
            self.state_machine.transition('show_results_context')
            self.send_message(user_id)
            return

        profile = self.state_machine.context_data['profiles_to_send'].pop()
        self.log.info(f"profile to see: {profile}")
        insert_seen_user_data(user_id, profile[3])

        self.state_machine.states['view_profile']['message'] = f'\n{profile[0]}  {profile[1]}  {profile[2]}'
        self.log.info(f"Фотографии для отправки: {profile[4]}")
        self.send_message(user_id, attachments=profile[4].split(','))

    def send_artificial_event(self, user_id, text):
        event = ArtificialEvent(user_id, text)
        self.process_event(event)

    def process_event(self, event):
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            # переходим в новое состояние
            self.state_machine.transition(event.text)
            # функции, которые должны выполниться перед показом сообщения состояния
            self.run_pre_hooks(event)

            # показываем сообщения от бота
            if self.state_machine.current_state == 'view_profile':
                self.view_next_profile(event.user_id)
            elif self.state_machine.current_state == 'show_results_context':
                self.view_next_profile(event.user_id)
            else:
                self.send_message(event.user_id)

            # функции, которые должны выполниться после показом сообщения состояния
            self.run_post_hooks(event)

    def run(self):
        for event in self.longpoll.listen():
            if isinstance(event, ArtificialEvent):
                self.process_event(event)
            elif isinstance(event, Event):
                self.process_event(event)


if __name__ == '__main__':
    bot = VKBotSearch()
    bot.run()
