import logging

import vk_api


class VkApiClient:
    user_url = f'https://vk.com/id'

    def __init__(self, session):
        self.user_vk_session = session
        self.log = logging.getLogger()

    def prepare_user_params(self, sex, age_from, age_to, city):
        params = {
            'v': '5.131',
            'sex': sex,
            'age_from': age_from,
            'age_to': age_to,
            'country_id': '1',
            'hometown': city,
            'fields': 'id, sex, bdate, city, relation',
            'status': '1' or '6',
            'count': 20,
            'has_photo': '1',
            'is_closed': False,
            'can_access_closed': True,
        }
        return params

    def get_vk_user_popular_photos(self, vk_user_id):
        try:
            photos = self.user_vk_session.method('photos.get', {
                'user_id': vk_user_id,
                'album_id': 'profile',
                'extended': 1,
                'count': 30,
                'v': '5.131'
            })
        except vk_api.VkApiError:
            self.log.error(f'Не удалось загрузить фотографии; VK API error: {e}')
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

    def perform_search(self, sex, age_from, age_to, city):
        search_result = []

        try:
            persons = self.user_vk_session.method('users.search', self.prepare_user_params(sex, age_from, age_to, city))
        except vk_api.VkApiError as e:
            self.log.error(f'VK API error: {e}')
            return []

        for person in persons.get('items'):
            person_dict = {
                'first_name': person['first_name'],
                'last_name': person['last_name'],
                'url': self.user_url + str(person['id']),
                'id': person['id'],
            }
            if not person['is_closed']:
                search_result.append(person_dict)
        return search_result
