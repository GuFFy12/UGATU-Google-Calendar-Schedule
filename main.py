import configparser
import time
import traceback
from datetime import datetime, timedelta
from typing import Optional

import pytz
import requests
from bs4 import BeautifulSoup
from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar


# Utilities

def every(delay, task, args):
    next_time = time.time() + delay
    while True:
        time.sleep(max(0, next_time - time.time()))
        try:
            task(*args)
        except Exception:
            traceback.print_exc()

        next_time += (time.time() - next_time) // delay * delay + delay


def get_first_day_of_first_september_week(current_date: datetime):
    first_september = pytz.timezone("Asia/Yekaterinburg").localize(datetime(current_date.year, 9, 1))

    if current_date < first_september:  # Если следующий учебный год не наступил, то считаем что сейчас предыдущий год.
        first_september = datetime(current_date.year - 1, 9, 1)

    day_of_week = first_september.weekday()

    return first_september - timedelta(days=day_of_week)


def get_date_from_schedule(first_day_of_first_september_week: datetime, week: int, day_of_week: str, lesson_time: str):
    index_day_of_week = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"].index(day_of_week)
    hours, minutes = map(int, lesson_time.split(":"))

    return first_day_of_first_september_week + timedelta(weeks=week - 1, days=index_day_of_week, hours=hours, minutes=minutes)


def get_lesson_num(lesson_start_time: str):
    return ["08:00", "09:35", "11:35", "13:10", "15:10", "16:45", "18:20", "19:55", "21:25", "22:55"].index(lesson_start_time) + 1


def get_event_color(lesson_type: str):
    event_colors = {
        "Лекция": "3",
        "Практика (семинар)": "6",
        "Лабораторная работа": "9",
        "Физвоспитание": "2",
        "Военная подготовка": "10",
        "Лекция + практика": "4",
        "Консультация": "7",
        "Экзамен": "11",
        "Консультация экзамена": "11",
        "Ликвидация задолженостей": "11",
        "Зачёт с оценкой": "11",
        "Зачёт": "11",
        "Защита (Курсовой/РГР/Лабораторной)": "11",
        "Лекция + практика + лабораторная работа": "1",
        "Мероприятие": "5",
        "Кураторский час": "5",
        "Прочее": "8",
    }

    event_color = event_colors.get(lesson_type)
    if event_color is None:
        print(f"Ошибка — Тип предмета '{lesson_type}' не найден в словаре")

    return event_color


def format_event_as_string(event: Event):
    return f"{datetime.strftime(event.start, '%d.%m.%Y %H:%M')}, {event.summary}, {event.location}, {event.description}"


def get_event_hash(event: Event):
    return str(hash((event.summary, event.description, event.location, datetime.strftime(event.start, '%d.%m.%Y %H:%M'))))


# Main Code

def get_schedule_events(schedule_semester_id: int, schedule_type: int, student_group_or_teacher_id: int,
                        minutes_before_popup_reminder_first_lesson: Optional[int], minutes_before_popup_reminder: Optional[int]):
    params = {
        "schedule_semestr_id": schedule_semester_id,
        "WhatShow": schedule_type,
        "weeks": 0,
    }

    if schedule_type == 1:
        params["student_group_id"] = student_group_or_teacher_id
    elif schedule_type == 2:
        params["teacher"] = student_group_or_teacher_id

    response = requests.get("https://isu.ugatu.su/api/new_schedule_api/", params)
    soup = BeautifulSoup(response.text, "html.parser")
    lesson_rows = soup.find("tbody").findAll("tr")

    schedule_events = {}

    current_date = datetime.now(pytz.timezone("Asia/Yekaterinburg"))
    first_day_of_first_september_week = get_first_day_of_first_september_week(current_date)

    day_of_week = ""
    day_first_lesson_number = {}
    for lesson_row in lesson_rows:
        lesson_columns = lesson_row.findAll("td")

        if "dayheader" in lesson_row["class"]:
            day_of_week = lesson_columns[0].text

        if "noinfo" in lesson_row["class"]:
            continue

        lesson_start_time, lesson_end_time = lesson_columns[1].text.split("-")
        lesson_weeks = lesson_columns[2].text.split()
        lesson_name = lesson_columns[3].text
        lesson_type = lesson_columns[4].text
        lesson_teacher_or_student_group = lesson_columns[5].text
        lesson_classroom = lesson_columns[6].text
        lesson_comment = lesson_columns[7].text

        for week in lesson_weeks:
            lesson_day_hash = str(hash((week, day_of_week)))
            lesson_number = get_lesson_num(lesson_start_time)

            lesson_end_date = get_date_from_schedule(first_day_of_first_september_week, int(week), day_of_week, lesson_end_time)
            if current_date > lesson_end_date:  # Не добавляем уже прошедшие занятия.
                if lesson_day_hash not in day_first_lesson_number:
                    day_first_lesson_number[lesson_day_hash] = lesson_number
                continue

            lesson_start_date = get_date_from_schedule(first_day_of_first_september_week, int(week), day_of_week, lesson_start_time)

            schedule_event = Event(
                f"{lesson_number}. {lesson_name} — {lesson_type}",
                description=("Преподаватель" if schedule_type == 1 else "Группа") + f": {lesson_teacher_or_student_group}" +
                            (f"\nКомментарий: {lesson_comment}" if lesson_comment != "" else ""),
                minutes_before_popup_reminder=(minutes_before_popup_reminder_first_lesson
                                               if (lesson_day_hash not in day_first_lesson_number or day_first_lesson_number[lesson_day_hash] == lesson_number)
                                               else minutes_before_popup_reminder),
                color_id=get_event_color(lesson_type),
                location=lesson_classroom,
                timezone="Asia/Yekaterinburg",
                start=lesson_start_date,
                end=lesson_end_date
            )

            schedule_events[get_event_hash(schedule_event)] = schedule_event

            if lesson_day_hash not in day_first_lesson_number:
                day_first_lesson_number[lesson_day_hash] = lesson_number

    return schedule_events


def sync_calendar_with_schedule(config: configparser.ConfigParser, gc: GoogleCalendar):
    gc_events = list(gc.get_events(timezone="Asia/Yekaterinburg"))

    schedule_events = get_schedule_events(
        int(config["Settings"]["schedule_semester_id"]),
        int(config["Settings"]["schedule_type"]),
        student_group_or_teacher_id=int(config["Settings"]["student_group_or_teacher_id"]),
        minutes_before_popup_reminder_first_lesson=(
            int(config["Settings"]["minutes_before_popup_reminder_first_lesson"]) if "minutes_before_popup_reminder_first_lesson" in config["Settings"] else None),
        minutes_before_popup_reminder=(int(config["Settings"]["minutes_before_popup_reminder"]) if "minutes_before_popup_reminder" in config["Settings"] else None)
    )

    for gc_event in gc_events:
        if get_event_hash(gc_event) not in schedule_events:  # Если в календаре хеш события, а в расписании нет, то считаем что пара удалена.
            gc.delete_event(gc_event, send_updates="all")
            print(f"Занятие удаленно — {format_event_as_string(gc_event)}")
            continue

        schedule_event = schedule_events[get_event_hash(gc_event)]

        is_event_updated = (gc_event.summary != schedule_event.summary or gc_event.description != schedule_event.description or gc_event.location != schedule_event.location or
                            gc_event.reminders != schedule_event.reminders)

        if is_event_updated:  # Если есть различия у занятия в расписании и календаре, обновляем событие.
            schedule_event.event_id = gc_event.event_id
            gc.update_event(schedule_event, send_updates="all")
            print(f"Занятие обновлено — {format_event_as_string(schedule_event)}")

        schedule_events.pop(get_event_hash(gc_event))  # Удаляем из списка расписания занятия, которые уже были в календаре.

    for schedule_event in schedule_events.values():
        gc.add_event(schedule_event, send_updates="all")
        print(f"Занятие добавлено — {format_event_as_string(schedule_event)}")


def main():
    config = configparser.ConfigParser()
    config.read("settings.ini")

    gc = GoogleCalendar(config["Settings"]["default_calendar"], credentials_path="client_secret.json")

    sync_calendar_with_schedule(config, gc)

    if config["Settings"]["task_scheduler_delay"] == "0":
        return

    every(int(config["Settings"]["task_scheduler_delay"]), sync_calendar_with_schedule, (config, gc))


if __name__ == "__main__":
    main()
