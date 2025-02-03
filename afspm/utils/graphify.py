"""Script to turn a config file into a graph visualization."""

import logging

import graphviz
import tomli
import fire

from afspm.utils.parser import (expand_variables_in_dict, CLASS_KEY,
                                IS_COMPONENT_KEY, IS_URL_KEY)
from afspm.utils.log import set_up_logging


logger = logging.getLogger(__name__)


NL = r'\n'  # raw character for newline (issues with gv file otherwise)

# Color style used for visualization
COLOR_STYLE = 'set19'
COLORS_LOOP = 10  # Number of colors in this style (for looping)
URL_CLUSTER = 'cluster_url'  # Name for cluster of urls in gv file
URLS_COLOR = '/greys3/1'
COMPONENT_PREPEND = 'cluster_'  # Need to prepend to be distinct UI element
COMPONENTS_COLOR = '/blues3/1'
NODES_COLOR = 'white'
EDGE_WIDTH = '2'

STAGGER_NUM = 5  # Stagger dot file for better rendering.


def graphify(config_file: str, gv_filepath: str = None,
             render_filepath: str = None, log_file: str = 'log.txt',
             log_to_stdout: bool = True, log_level: str = "INFO"):
    """Convert a config file to a graphviz representation.

    This method converts a provided config file into a graphviz representation,
    so it can be visually inspected for issues. It exists because config files
    can become massive and hard to parse visually. By converting to a
    node-based image, we can more easily check to ensure all of our components
    are talking to each other in the manner we want.

    It accomplishes this by a two step procedure:
    1. The config file is converted into 'dot' source code, the (most common)
    language supported by graphviz.
    2. The dot source code is rendered into a visual format.

    For both steps (1) and (2), the method can output the created file.
    However, only the rendered file is created by default.

    Args:
        config_file: path to TOML config file.
        gv_filepath: the desired path to write the created dot file to. Defaults
            to False (in which case it writes to the same filepath and filename
            as config_file, with the extension replaced with .gv).
        render_filepath: the desired path to write the created rendered file to.
            Defaults to False (in which case it writes to the same filepath and
            filename as config_file, with the extension replaced with .pdf).
        log_file: a file path to save the process log. Default is 'log.txt'.
            To not log to file, set to None.
        log_to_stdout: whether or not we print to std out as well. Default is
            True.
        log_level: the log level to use. Default is INFO.

    Returns:
        None.
    """
    log_init_method = set_up_logging
    log_args = (log_file, log_to_stdout, log_level)
    log_init_method(*log_args)

    with open(config_file, 'rb') as file:
        config_dict = tomli.load(file)

    if config_dict is None:
        logger.error("Config file not found or loading failed, exiting.")
        return

    # Create default filepaths if None provided.
    filebase = config_file.rsplit('.')[0]
    if not gv_filepath:
        gv_filepath = filebase + '.gv'
    if not render_filepath:
        render_filepath = filebase + '.pdf'

    dot = graphviz.Graph(name=config_file)
    expanded_dict = expand_variables_in_dict(config_dict)

    # First pass: get main urls and put into special cluster
    url_colors_dict = {}
    color_idx = 0
    with dot.subgraph(name=URL_CLUSTER) as sg:
        sg.attr(style='filled', color=URLS_COLOR)
        sg.node_attr.update(style='filled', color=NODES_COLOR)
        sg.attr(label='urls')

        for key, val in expanded_dict.items():
            # First, get URLs and make them independent nodes
            if IS_URL_KEY in key:
                clean_url = _sanitize_url_name(val)
                url_colors_dict[clean_url] = _new_url_color(color_idx)
                color_idx = _update_color_idx(color_idx)
                sg.node(clean_url, label=val,
                        color=url_colors_dict[clean_url])

    # Now, go through components and create clusters for each.
    for key, val in expanded_dict.items():
        if _is_component(val):
            component = val
            component_name = COMPONENT_PREPEND + key
            component_label = (f'{key}' + NL +
                               f'[{_get_class_simple_name(val[CLASS_KEY])}]')
            with dot.subgraph(name=component_name) as sg:
                sg.attr(style='filled', color=COMPONENTS_COLOR)
                sg.node_attr.update(style='filled', color=NODES_COLOR)
                sg.edge_attr['penwidth'] = EDGE_WIDTH
                sg.attr(label=component_label)

                for comp_key, comp_val in component.items():
                    urls = _get_urls_if_io_node(comp_val)

                    if len(urls) > 0:
                        io_node_name = key + '_' + comp_key
                        io_node_label = (
                            f'{comp_key}' + NL +
                            f'[{_get_class_simple_name(comp_val[CLASS_KEY])}]')
                        sg.node(io_node_name, label=io_node_label)
                        for url in urls:
                            # First, create a url node if it does not exist
                            clean_url = _sanitize_url_name(url)

                            # Handle edge case where new url is found (we
                            # should have found all with the first pass).
                            if clean_url not in url_colors_dict.keys():
                                logger.error(f'url {url}' +
                                             'not already declared!' +
                                             'for now, creating new one.')
                                dot.node(clean_url, label=url)
                                url_colors_dict[clean_url] = _new_url_color(
                                    color_idx)
                                color_idx = _update_color_idx(color_idx)

                            # Edge from IO node to the url value
                            sg.edge(io_node_name, clean_url,
                                    color=url_colors_dict[clean_url])
    logger.trace(f'Dot source before staggering: {dot.source}')
    dot = dot.unflatten(stagger=STAGGER_NUM)
    logger.debug(f'Dot source: {dot.source}')
    dot.render(filename=gv_filepath, outfile=render_filepath)


def _get_class_simple_name(class_name: str) -> str:
    """Extract simplified class name, for visualization purposes.

    Grabs the last part of the class name, i.e. removes any package/module
    definitions. Used to simplify visualizations.

    Args:
        class_name: full class name.

    Returns:
        simplified class name.
    """
    return class_name.split('.')[-1]


def _sanitize_url_name(url_name: str) -> str:
    """Convert urls to dot-supported names.

    Dot files do not support names with colons, as this is used internally for
    some other purpose. Thus, this method will replace colons with underscores.

    Args:
        url_name: input url, which may contain colons.

    Returns:
        modified string, with colons replaced by underscores.
    """
    url_name = url_name.replace('/', '')
    url_name = url_name.replace('.', '_')
    return url_name.replace(':', '__')


def _new_url_color(idx: int) -> str:
    """Output str color name for a given index.

    Uses Brewer's pastel19 style.

    Args:
        idx: number of color to output.

    Returns:
        str of pastel19 style, to be used by graphviz.
    """
    return '/' + COLOR_STYLE + '/' + str(idx + 1)


def _update_color_idx(idx: int) -> int:
    """Update color index.

    Uses Brewer's pastel19 style.  Warns the user if we have
    exceeded the number of colors supported by our style.

    Args:
        idx: current color idx.

    Returns:
        new index value.
    """
    new_idx = (idx + 1)
    if new_idx >= COLORS_LOOP:
        logger.error('Exceeded color values of ' + COLOR_STYLE +
                     '. We are now re-using colors. Keep in mind when ' +
                     'looking at render.')
    new_idx = new_idx % COLORS_LOOP
    return new_idx


def _is_component(val) -> bool:
    """Check if the provided input is a component.

    We define a component from our parsed config file as a dict with (a) a
    'component' key, and (b) a 'class' key.

    Args:
        val: what we checking.

    Returns:
        True if it meets our criteria.
    """
    return (isinstance(val, dict) and
            IS_COMPONENT_KEY in val.keys() and CLASS_KEY in val.keys())


def _get_urls_if_io_node(io_node) -> list[str]:
    """If the provided val is an IO node, return list of urls.

    We define an IO node from our parsed config file as a dict with (a) a
    'class' key, and (b) one or more keys in it's dict (or a subdict) with the
    substr 'url'.

    So, we recursively go into this dict and its subdict, extracting the vals
    of key:val pairs that have keys with 'url' in it.

    Args:
        io_node: what we checking.

    Returns:
        - An empty list if val is not a dict and does not have a 'class' key.
        - A list of vals from this dict (or subdicts) whose 'key' has 'url' in
            it.
    """
    if not (isinstance(io_node, dict) and CLASS_KEY in io_node.keys()):
        return []

    logger.trace('Getting all urls from an io node.')

    def __get_urls(maybe_dict):
        other_urls = []
        if isinstance(maybe_dict, list) or isinstance(maybe_dict, tuple):
            logger.trace('Iterating through items in list, tuple')
            for val in maybe_dict:
                other_urls.extend(__get_urls(val))
        if isinstance(maybe_dict, dict):
            for key, val in maybe_dict.items():
                logger.trace(f'Entering dict of key {key}')
                other_urls.extend(__get_urls(val))
            urls = [kv[1] for kv in maybe_dict.items() if IS_URL_KEY in kv[0]]
            logger.trace(f'Got urls from this dict: {urls}')
            other_urls.extend(urls)
        return other_urls

    all_urls = __get_urls(io_node)
    logger.trace(f'all_urls: {all_urls}')
    return all_urls


def cli_graphify():
    """Call graphify via command-line interface."""
    fire.Fire(graphify)


if __name__ == '__main__':
    cli_graphify()
