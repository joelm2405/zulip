from datetime import datetime
from typing import Annotated, Literal

from django.http import HttpRequest, HttpResponse
from django.utils.timezone import now as timezone_now
from django.utils.translation import gettext as _
from pydantic import Json, StringConstraints

from zerver.actions.user_topics import do_set_user_topic_visibility_policy
from zerver.lib.response import json_success
from zerver.lib.streams import (
    access_stream_by_id,
    access_stream_by_name,
    access_stream_to_remove_visibility_policy_by_id,
    access_stream_to_remove_visibility_policy_by_name,
    check_for_exactly_one_stream_arg,
)
from zerver.lib.typed_endpoint import typed_endpoint
from zerver.lib.typed_endpoint_validators import check_int_in_validator
from zerver.models import UserProfile, UserTopic
from zerver.models.constants import MAX_TOPIC_NAME_LENGTH


def mute_topic(
    user_profile: UserProfile,
    stream_id: int | None,
    stream_name: str | None,
    topic_name: str,
    date_muted: datetime,
) -> None:
    if stream_name is not None:
        (stream, sub) = access_stream_by_name(user_profile, stream_name)
    else:
        assert stream_id is not None
        (stream, sub) = access_stream_by_id(user_profile, stream_id)

    do_set_user_topic_visibility_policy(
        user_profile,
        stream,
        topic_name,
        visibility_policy=UserTopic.VisibilityPolicy.MUTED,
        last_updated=date_muted,
    )


def unmute_topic(
    user_profile: UserProfile,
    stream_id: int | None,
    stream_name: str | None,
    topic_name: str,
) -> None:
    error = _("Topic is not muted")

    if stream_name is not None:
        stream = access_stream_to_remove_visibility_policy_by_name(user_profile, stream_name, error)
    else:
        assert stream_id is not None
        stream = access_stream_to_remove_visibility_policy_by_id(user_profile, stream_id, error)

    do_set_user_topic_visibility_policy(
        user_profile, stream, topic_name, visibility_policy=UserTopic.VisibilityPolicy.INHERIT
    )


@typed_endpoint
def update_muted_topic(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_id: Json[int] | None = None,
    stream: str | None = None,
    topic: Annotated[str, StringConstraints(max_length=MAX_TOPIC_NAME_LENGTH)],
    op: Literal["add", "remove"],
) -> HttpResponse:
    check_for_exactly_one_stream_arg(stream_id=stream_id, stream=stream)

    if op == "add":
        mute_topic(
            user_profile=user_profile,
            stream_id=stream_id,
            stream_name=stream,
            topic_name=topic,
            date_muted=timezone_now(),
        )
    elif op == "remove":
        unmute_topic(
            user_profile=user_profile,
            stream_id=stream_id,
            stream_name=stream,
            topic_name=topic,
        )
    return json_success(request)


@typed_endpoint
def update_user_topic(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_id: Json[int],
    topic: Annotated[str, StringConstraints(max_length=MAX_TOPIC_NAME_LENGTH)],
    visibility_policy: Json[
        Annotated[int, check_int_in_validator(UserTopic.VisibilityPolicy.values)]
    ],
) -> HttpResponse:
    if visibility_policy == UserTopic.VisibilityPolicy.INHERIT:
        error = _("Invalid channel ID")
        stream = access_stream_to_remove_visibility_policy_by_id(user_profile, stream_id, error)
    else:
        (stream, sub) = access_stream_by_id(user_profile, stream_id)

    do_set_user_topic_visibility_policy(
        user_profile, stream, topic, visibility_policy=visibility_policy
    )
    return json_success(request)


@typed_endpoint
def get_stream_topic_counts(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    stream_id: Json[int],
) -> HttpResponse:
    from zerver.models import Stream, Message, UserTopic

    try:
        stream = Stream.objects.get(id=stream_id)
    except Stream.DoesNotExist:
        from zerver.lib.response import json_error
        return json_error(_("Invalid stream ID"))

    recipient_id = stream.recipient_id

    # Paso 1: obtener todos los nombres de tópicos del canal (distintos)
    topic_names = (
        Message.objects.filter(
            recipient_id=recipient_id,
            is_channel_message=True,
        )
        .values_list("subject", flat=True)
        .distinct()
    )

    # Paso 2: contar cuántos existen
    total_topics = len(topic_names)

    # Paso 3: contar cuántos están marcados como FOLLOWED por el usuario
    followed_topics = UserTopic.objects.filter(
        user_profile=user_profile,
        stream=stream,
        visibility_policy=UserTopic.VisibilityPolicy.FOLLOWED,
        topic_name__in=topic_names,
    ).count()

    return json_success(
        request,
        data={
            "stream_id": stream_id,
            "total_topics": total_topics,
            "followed_topics": followed_topics,
        },
    )
