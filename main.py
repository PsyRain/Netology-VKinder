import logging
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard as BaseVkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id
from transitions import Machine

from api import VkApiClient
from database import create_seen_users_table, insert_seen_user_data, select_seen_user
from secrets import group_token, user_access_token
from states import states, transitions


logging.basicConfig(level=logging.INFO)


class VkKeyboard(BaseVkKeyboard):
    def add_button(self, label, color=VkKeyboardColor.SECONDARY, payload=None):
        """Добавляем текучий интерфейс."""
        super().add_button(label, color, payload)
        return self


def prepare_attachments(profile_pics):
    attach = []
    for pic in profile_pics['pics_ids']:
        attach.append(f'photo{profile_pics["owner_id"]}_{pic}')
    return attach


class VKBotSearch:

    def __init__(self, group_token, token):
        self.token = token
        self.vk_session = vk_api.VkApi(token=group_token)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkLongPoll(self.vk_session)
        self.vk_client = VkApiClient(session=vk_api.VkApi(token=token))
        self.log = logging.getLogger()
        self.log.info('Бот запущен')

        # для описания состояний и переходов между ними используется библиотека transitions
        # см. https://github.com/pytransitions/transitions
        self.machine = Machine(
            model=self, states=states, transitions=transitions, initial="START", queued=True
        )

        self._event = None
        self.user_search_preference = {}
        self.profiles_to_send = {}

    def create_db(self):
        # хук для transitions; создаем БД, если ее нет
        create_seen_users_table()

    def is_hello_message_valid(self):
        return self._event.text.lower() == "привет"

    def is_gender_valid(self):
        return self._event.text.lower() in ["м", "ж"]

    def is_age_valid(self, type):
        age = self._event.text
        try:
            age = int(age)
        except ValueError:
            return False

        if type == 'min':
            return age >= 18
        elif type == 'max':
            return age <= 100

    def get_preferred_sex(self, user_id):
        preferred_sex = self.user_search_preference[user_id]['preferred_sex']

        if preferred_sex == 'М':
            return "2"
        elif preferred_sex == 'Ж':
            return "1"

    def set_preferred_sex(self):
        event = self._event
        if event.user_id not in self.user_search_preference:
            self.user_search_preference[event.user_id] = {}

        self.user_search_preference[event.user_id]['preferred_sex'] = event.text
        self.log.info(f'{self.user_search_preference=}')

    def _ensure_user_preference(self, user_id):
        if user_id not in self.user_search_preference:
            self.user_search_preference[user_id] = {}

    def set_preferred_age(self, type):
        event = self._event
        user_id = event.user_id
        self._ensure_user_preference(user_id)

        self.user_search_preference[user_id][f"preferred_{type}_age"] = int(event.text)
        self.log.info(f'{type} age set as {event.text} for user id {user_id}')

    def set_preferred_city(self):
        event = self._event
        user_id = event.user_id
        self._ensure_user_preference(user_id)

        self.user_search_preference[user_id][f"preferred_city"] = event.text
        self.log.info(f'city set as {event.text} for user id {user_id}')

    def filter_unseen_users(self, profiles, user_id):
        profiles_to_send = []
        for profile in profiles:
            if select_seen_user(user_id, profile['id']) is None:  # проверяем нет ли повторений
                profiles_to_send.append(profile)
        return profiles_to_send

    def get_suggested_candidates(self, event):
        user_id = event.user_id
        profiles = self.vk_client.perform_search(
            sex=self.get_preferred_sex(user_id),
            age_from=self.user_search_preference[user_id]['preferred_min_age'],
            age_to=self.user_search_preference[user_id]['preferred_max_age'],
            city=self.user_search_preference[user_id]['preferred_city'],
        )
        self.log.info(f'Количество найденных профилей для пользователя #{user_id}: {len(profiles)}')
        filtered_profiles = self.filter_unseen_users(profiles, user_id)
        self.profiles_to_send[user_id] = filtered_profiles

    def send_message(self, event, text=None, attachments=None, keyboard=None):
        user_id = event.user_id
        text_msg = text
        if not text_msg:
            text_msg = '...'
        self.vk.messages.send(
            user_id=user_id,
            message=text_msg,
            keyboard=keyboard,
            attachment=','.join(attachments) if attachments else None,
            random_id=get_random_id()
        )

    def get_invalid_input_message(self):
        if self.state == 'START':
            return "Начните сообщение со слова 'привет'"
        elif self.state == 'SET_GENDER':
            return "Пожалуйства, введите корректный пол"
        elif self.state == 'SET_MIN_AGE':
            return "Пожалуйста, введите корректный возраст. От 18 лет."
        elif self.state == 'SET_MAX_AGE':
            return "Пожалуйста, введите корректный возраст. До 100 лет."
        else:
            return "Неправильный ввод."

    def view_next_profile(self, event):
        user_id = event.user_id

        if self.profiles_to_send[user_id]:
            profile = self.profiles_to_send[user_id].pop()
            self.log.info(f"profile to see: {profile}")
            insert_seen_user_data(user_id, profile['id'])

            return profile

    def handle_message(self, event):
        user_id = event.user_id

        message = event.text

        self._event = event

        match self.state:
            case 'START':
                if self.is_hello_message_valid():
                    self.start_and_choose_gender()  # триггер, переход в следующее состояние (см. аттрибут transitions
                    # и документацию библиотеки transitions https://github.com/pytransitions/transitions )
                    self.send_message(
                        event,
                        text="Привет! Кто ты ищешь? Мужчину или женщину? (напиши М или Ж)",
                        keyboard=VkKeyboard(inline=True)
                            .add_button('М', color=VkKeyboardColor.POSITIVE)
                            .add_button('Ж', color=VkKeyboardColor.POSITIVE)
                            .get_keyboard()
                        )
                else:
                    error_msg = self.get_invalid_input_message()
                    self.send_message(event, text=error_msg)
            case "SET_GENDER":
                if self.is_gender_valid():
                    self.set_preferred_sex()

                    if message == "м":
                        message = "Отлично! Напиши от какого возраста мужчину ты ищешь."
                    else:
                        message = "Отлично! Напиши от какого возраста женщину ты ищешь."

                    self.choose_min_age()  # переходим в следующее состояние
                    self.send_message(event, text=message)
                else:
                    error_msg = self.get_invalid_input_message()
                    self.send_message(event, text=error_msg)
            case "SET_MIN_AGE":
                if self.is_age_valid(type='min'):
                    self.set_preferred_age(type='min')
                    self.choose_max_age()  # переходим в следующее состояние
                    self.send_message(event, "Отлично! Теперь укажи до какого возраста ты ищешь")
                else:
                    error_msg = self.get_invalid_input_message()
                    self.send_message(event, text=error_msg)
            case "SET_MAX_AGE":
                if self.is_age_valid(type='max'):
                    self.set_preferred_age(type='max')
                    self.choose_city()  # переходим в следующее состояние
                    self.send_message(event, "Введи город, в котором ты хочешь искать партнера")
                else:
                    error_msg = self.get_invalid_input_message()
                    self.send_message(event, text=error_msg)
            case "SET_CITY":
                self.set_preferred_city()
                self.start_search()  # переходим в следующее состояние
                self.send_message(
                    event,
                    keyboard=VkKeyboard(inline=True)
                    .add_button('Начать поиск', color=VkKeyboardColor.POSITIVE).get_keyboard()
                )
            case "SEARCHING":
                self.get_suggested_candidates(event)
                self.send_message(
                    event, text="Поиск окончен",
                    keyboard=VkKeyboard(inline=True)
                    .add_button('Посмотреть результаты', color=VkKeyboardColor.POSITIVE).get_keyboard()
                )
                self.show_results()  # переходим в следующее состояние
            case "SHOW_RESULTS":
                profile = self.view_next_profile(event)
                if profile:
                    attach = prepare_attachments(
                        self.vk_client.get_vk_user_popular_photos(profile['id'])
                    )
                    self.send_message(
                        event,
                        text=f"\n{profile['first_name']} {profile['last_name']} {profile['url']}",
                        attachments=attach
                    )
                    self.send_message(
                        event, text='',
                        keyboard=VkKeyboard(inline=True)
                        .add_button('Следующий профиль', color=VkKeyboardColor.POSITIVE).get_keyboard()
                    )
                    self.next_profile()  # переходим в следующее состояние
                else:
                    self.log.info(f"Пользователь {user_id} просмотрел все профили")
                    self.send_message(
                        event,
                        text='Все профили просмотрены.',
                        keyboard=VkKeyboard(inline=True)
                        .add_button('Закончить разговор', color=VkKeyboardColor.SECONDARY).get_keyboard()
                    )
                    self.finish()  # переходим в следующее состояние

            case 'FINISH':
                self.to_start()  # переходим в следующее состояние
                self.send_message(
                    event, text='Спасибо за пользование ботом!',
                    keyboard=VkKeyboard(inline=True)
                    .add_button('Начать новый поиск', color=VkKeyboardColor.POSITIVE).get_keyboard()
                )

    def run(self):
        for event in self.longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                self.handle_message(event)


if __name__ == '__main__':
    bot = VKBotSearch(group_token, user_access_token)
    bot.run()
