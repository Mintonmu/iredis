import re
import sys
import time
import logging

from prompt_toolkit.formatted_text import FormattedText

from iredis.exceptions import InvalidArguments


logger = logging.getLogger(__name__)

_last_timer = time.time()
_timer_counter = 0
sperator = re.compile(r"\s")
logger.debug(f"[timer] start on {_last_timer}")


def timer(title):
    global _last_timer
    global _timer_counter

    now = time.time()
    tick = now - _last_timer
    logger.debug(f"[timer{_timer_counter:2}] {tick:.8f} -> {title}")

    _last_timer = now
    _timer_counter += 1


def nativestr(x):
    return x if isinstance(x, str) else x.decode("utf-8", "replace")


def literal_bytes(b):
    if isinstance(b, bytes):
        return str(b)[2:-1]
    return b


def _valide_token(words):
    token = "".join(words).strip()
    if token:
        yield token


def _strip_quote_args(s):
    """
    Given string s, split it into args.(Like bash paring)
    Handle with all quote cases.

    Raise ``InvalidArguments`` if quotes not match

    :return: args list.
    """
    word = []
    in_quote = None
    pre_back_slash = False
    for char in s:
        if in_quote:
            # close quote
            if char == in_quote:
                if not pre_back_slash:
                    yield "".join(word)
                    word = []
                    in_quote = None
                else:
                    # previous char is \ , merge with current "
                    word[-1] = char
            else:
                word.append(char)
        # not in quote
        else:
            # sperator
            if sperator.match(char):
                if word:
                    yield "".join(word)
                    word = []
            # open quotes
            elif char in ["'", '"']:
                in_quote = char
            else:
                word.append(char)
        if char == "\\" and not pre_back_slash:
            pre_back_slash = True
        else:
            pre_back_slash = False

    if word:
        yield "".join(word)
    # quote not close
    if in_quote:
        raise InvalidArguments("Invalid argument(s)")


def split_command_args(command, all_commands):
    """
    Split Redis command text into command and args.

    :param command: redis command string, with args
    :param all_commands: full redis commands list
    """
    command = command.strip()
    upper_command_list = command.upper().split()
    for command_name in all_commands:
        _command_name = command_name.split()
        _command_length = len(_command_name)
        if upper_command_list[:_command_length] == _command_name:
            input_command = " ".join(command.split()[:_command_length])
            input_args = " ".join(command.split()[_command_length:])
            break
    else:
        raise InvalidArguments(f"`{command}` is not a valide Redis Command")

    args = list(_strip_quote_args(input_args))

    return input_command, args


type_convert = {"posix time": "time"}


def parse_argument_to_formatted_text(
    name, _type, is_option, style_class="bottom-toolbar"
):
    result = []
    if isinstance(name, str):
        _type = type_convert.get(_type, _type)
        result.append((f"class:{style_class}.{_type}", " " + name))
    elif isinstance(name, list):
        for inner_name, inner_type in zip(name, _type):
            inner_type = type_convert.get(inner_type, inner_type)
            if is_option:
                result.append((f"class:{style_class}.{inner_type}", f" [{inner_name}]"))
            else:
                result.append((f"class:{style_class}.{inner_type}", f" {inner_name}"))
    else:
        raise Exception()
    return result


def compose_command_syntax(command_info, style_class="bottom-toolbar"):
    command_style = f"class:{style_class}.command"
    const_style = f"class:{style_class}.const"
    args = []
    if command_info.get("arguments"):
        for argument in command_info["arguments"]:
            if argument.get("command"):
                # command [
                args.append((command_style, " [" + argument["command"]))
                if argument.get("enum"):
                    enums = "|".join(argument["enum"])
                    args.append((const_style, f" [{enums}]"))
                elif argument.get("name"):
                    args.extend(
                        parse_argument_to_formatted_text(
                            argument["name"],
                            argument["type"],
                            argument.get("optional"),
                            style_class=style_class,
                        )
                    )
                # ]
                args.append((command_style, "]"))
            elif argument.get("enum"):
                enums = "|".join(argument["enum"])
                args.append((const_style, f" [{enums}]"))

            else:
                args.extend(
                    parse_argument_to_formatted_text(
                        argument["name"],
                        argument["type"],
                        argument.get("optional"),
                        style_class=style_class,
                    )
                )
    return args


def command_syntax(command, command_info):
    """
    Get command syntax based on redis-doc/commands.json

    :param command: Command name in uppercase
    :param command_info: dict loaded from commands.json, only for
        this command.
    """
    comamnd_group = command_info["group"]
    bottoms = [
        ("class:bottom-toolbar.group", f"({comamnd_group}) "),
        ("class:bottom-toolbar.command", f"{command}"),
    ]  # final display FormattedText

    bottoms += compose_command_syntax(command_info)

    if "since" in command_info:
        since = command_info["since"]
        bottoms.append(("class:bottom-toolbar.since", f"   since: {since}"))
    if "complexity" in command_info:
        complexity = command_info["complexity"]
        bottoms.append(("class:bottom-toolbar.complexity", f" complexity:{complexity}"))

    return FormattedText(bottoms)


def _literal_bytes(b):
    """
    convert bytes to printable text.

    backslash and double-quotes will be escaped by
    backslash.
    "hello\" -> \"hello\\\"

    we don't add outter double quotes here, since
    completer also need this function's return value
    to patch completers.

    b'hello' -> "hello"
    b'double"quotes"' -> "double\"quotes\""
    """
    s = str(b)
    s = s[2:-1]  # remove b' '
    # unescape single quote
    s = s.replace(r"\'", "'")
    return s


def ensure_str(origin, decode=None):
    """
    Ensure is string, for display and completion.

    Then add double quotes

    Note: this method do not handle nil, make sure check (nil)
          out of this method.
    """
    if origin is None:
        return None
    if isinstance(origin, str):
        return origin
    if isinstance(origin, int):
        return str(origin)
    elif isinstance(origin, list):
        return [ensure_str(b) for b in origin]
    elif isinstance(origin, bytes):
        if decode:
            return origin.decode(decode)
        return _literal_bytes(origin)
    else:
        raise Exception(f"Unkown type: {type(origin)}, origin: {origin}")


def double_quotes(unquoted):
    """
    Display String like redis-cli.
    escape inner double quotes.
    add outter double quotes.

    :param unquoted: list, or str
    """
    if isinstance(unquoted, str):
        # escape double quote
        escaped = unquoted.replace('"', '\\"')
        return f'"{escaped}"'  # add outter double quotes
    elif isinstance(unquoted, list):
        return [double_quotes(item) for item in unquoted]


def exit():
    """
    Exit IRedis REPL
    """
    print("Goodbye!")
    sys.exit()
