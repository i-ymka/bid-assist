# bid-assist/modules/bidder.py

import logging
import requests
from typing import Tuple

from config import FREELANCER_OAUTH_TOKEN
from constants import FREELANCER_API_BASE_URL, BIDS_ENDPOINT

def send_bid(project_id: int, bid_text: str, amount: int, period: int = 1) -> Tuple[bool, str]:
    """
    Отправляет ставку (bid) на проект через API Freelancer.

    Args:
        project_id: ID проекта.
        bid_text: Текст отклика (описание).
        amount: Сумма ставки.
        period: Срок выполнения в днях (по умолчанию 1).

    Returns:
        Кортеж (успех: bool, сообщение: str).
    """
    headers = {
        "Freelancer-OAuth-V1": FREELANCER_OAUTH_TOKEN,
        "Content-Type": "application/json",
    }

    # Тело запроса, как того требует API
    payload = {
        "project_id": project_id,
        "amount": amount,
        "period": period,
        "description": bid_text,
    }

    url = f"{FREELANCER_API_BASE_URL}{BIDS_ENDPOINT}"
    logging.info(f"Отправка ставки на проект ID {project_id} с суммой {amount}.")

    try:
        response = requests.post(url, headers=headers, json=payload, verify=True)
        response.raise_for_status() # Вызовет исключение для кодов 4xx/5xx

        data = response.json()
        if data.get("status") == "success":
            logging.info(f"Ставка на проект ID {project_id} успешно размещена.")
            return True, "Ставка успешно размещена! ✔️"
        else:
            error_message = data.get('message', 'Неизвестная ошибка от API.')
            logging.error(f"API Freelancer вернуло ошибку при ставке на ID {project_id}: {error_message}")
            return False, f"Ошибка от API: {error_message}"

    except requests.exceptions.HTTPError as e:
        error_details = e.response.json()
        error_code = error_details.get("error_code", "N/A")
        logging.error(f"HTTP ошибка при отправке ставки на ID {project_id}: {e.response.status_code} - {error_code}")
        return False, f"Ошибка сети: {e.response.status_code} ({error_code})"
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при отправке ставки на ID {project_id}: {e}")
        return False, "Непредвиденная ошибка."