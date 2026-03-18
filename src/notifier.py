"""Brevo transactional email notifications for earlier test slots."""

import os
import logging
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

from src.config import BREVO_API_KEY, NOTIFY_EMAIL, FROM_EMAIL, CURRENT_TEST_DATE

log = logging.getLogger(__name__)


def send_notification(earliest_date: str) -> bool:
    """
    Send an email notification about an earlier test slot.

    Args:
        earliest_date: the available slot date (YYYY-MM-DD)

    Returns:
        True if email sent successfully, False otherwise.
    """
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_API_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    html = f"""
    <div style="font-family: system-ui, -apple-system, sans-serif; max-width: 520px; margin: 0 auto;">
        <h2 style="color: #00703c; border-bottom: 3px solid #00703c; padding-bottom: 10px;">
            Earlier Driving Test Slot Found
        </h2>
        <p style="font-size: 16px;">
            An earlier test slot is available on
            <strong style="font-size: 20px; color: #00703c;">{earliest_date}</strong>
        </p>
        <p style="color: #555;">
            Your current test is booked for <strong>{CURRENT_TEST_DATE}</strong>.
        </p>
        <p>Log in immediately to book it before it goes:</p>
        <a href="https://driverpracticaltest.dvsa.gov.uk/login"
           style="background: #00703c; color: white; padding: 14px 28px;
                  text-decoration: none; border-radius: 4px; display: inline-block;
                  font-weight: 600; font-size: 16px; margin: 12px 0;">
            Book Now on DVSA
        </a>
        <hr style="border: none; border-top: 1px solid #ddd; margin: 24px 0;">
        <p style="color: #999; font-size: 12px;">
            Sent by your Raspberry Pi test checker.
        </p>
    </div>
    """

    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": NOTIFY_EMAIL}],
        sender={"email": FROM_EMAIL, "name": "DVSA Slot Checker"},
        subject=f"Earlier Test Slot Available: {earliest_date}",
        html_content=html,
    )

    try:
        api.send_transac_email(email)
        log.info(f"Notification sent for slot: {earliest_date}")
        return True
    except ApiException as e:
        log.error(f"Brevo API error: {e}")
        return False
