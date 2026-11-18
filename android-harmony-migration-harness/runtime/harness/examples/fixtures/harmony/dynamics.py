"""Synthetic production module for the offline example; not ArkTS migration."""


def gift_quantities():
    return [1, 10, 100]


def merge_pages(first, second):
    return list(dict.fromkeys(first + second))
