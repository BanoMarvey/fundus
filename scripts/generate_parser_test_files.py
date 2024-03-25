import subprocess
from argparse import ArgumentParser, Namespace
from logging import WARN
from typing import List, Optional

from tqdm import tqdm

from fundus import BaseCrawler, Crawler, PublisherCollection
from fundus.logging import basic_logger
from fundus.publishers.base_objects import PublisherEnum
from fundus.scraping.article import Article
from fundus.scraping.html import FundusSource
from fundus.scraping.scraper import Scraper
from tests.test_parser import attributes_required_to_cover
from tests.utility import HTMLTestFile, get_test_case_json, load_html_test_file_mapping


def get_test_article(enum: PublisherEnum, url: Optional[str] = None) -> Optional[Article]:
    crawler: BaseCrawler
    if url is None:
        crawler = Crawler(enum)
    else:
        source = FundusSource([url], publisher=enum.publisher_name)
        scraper = Scraper(source, parser=enum.parser)
        crawler = BaseCrawler(scraper)
    return next(crawler.crawl(max_articles=1, error_handling="suppress", only_complete=True), None)


def parse_arguments() -> Namespace:
    parser = ArgumentParser(
        prog="generate_parser_test_files",
        description=(
            "script to generate/update/overwrite test cases for parser unit tests. "
            "by default this will only generate files which do not exist yet. "
            "every changed/added file will automatically be added to git."
        ),
    )
    parser.add_argument(
        "attributes",
        metavar="A",
        nargs="*",
        help=(
            "the attributes which should be used to create test cases. "
            f"default: {', '.join(attributes_required_to_cover)}"
        ),
    )
    parser.add_argument("-p", dest="publishers", metavar="P", nargs="+", help="only consider given publishers")
    parser.add_argument(
        "-u",
        "--urls",
        metavar="U",
        nargs="+",
        help="use given URL instead of searching for an article. if set the urls will be mapped to the order of -p",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-o",
        "--overwrite",
        action="store_true",
        help="overwrite existing html and json files for the latest parser version",
    )
    group.add_argument(
        "-oj",
        "--overwrite_json",
        action="store_true",
        help="parse from existing html and overwrite existing json content",
    )

    arguments = parser.parse_args()

    if arguments.urls is not None:
        if arguments.publishers is None:
            parser.error("-u requires -p. you can only specify URLs when also specifying publishers.")
        if len(arguments.urls) != len(arguments.publishers):
            parser.error("-u and -p do not have the same argument length")

    return arguments


def main() -> None:
    arguments = parse_arguments()

    # sort args.attributes for consistency
    arguments.attributes = sorted(set(arguments.attributes) or attributes_required_to_cover)

    basic_logger.setLevel(WARN)

    publishers: List[PublisherEnum] = (
        list(PublisherCollection)
        if arguments.publishers is None
        else [PublisherCollection[pub] for pub in arguments.publishers]
    )

    urls = arguments.urls if arguments.urls is not None else [None] * len(publishers)

    with tqdm(total=len(publishers)) as bar:
        for url, publisher in zip(urls, publishers):
            bar.set_description(desc=publisher.name, refresh=True)

            # load json
            test_data_file = get_test_case_json(publisher)
            test_data = content if (content := test_data_file.load()) and not arguments.overwrite_json else {}

            # load html
            html_mapping = load_html_test_file_mapping(publisher) if not arguments.overwrite else {}

            if arguments.overwrite or not html_mapping.get(publisher.parser.latest_version):
                if not (article := get_test_article(publisher, url)):
                    basic_logger.warning(f"Couldn't get article for {publisher.name}. Skipping")
                    continue
                html = HTMLTestFile(
                    url=article.html.responded_url,
                    content=article.html.content,
                    crawl_date=article.html.crawl_date,
                    publisher=publisher,
                )
                html.write()
                subprocess.call(["git", "add", html.path], stdout=subprocess.PIPE)

                html_mapping[publisher.parser.latest_version] = html
                test_data[publisher.parser.latest_version.__name__] = {}

            for html in html_mapping.values():
                versioned_parser = html.publisher.parser(html.crawl_date)
                extraction = versioned_parser.parse(html.content)
                missing_attributes = set(arguments.attributes) - set(
                    test_data.get(type(versioned_parser).__name__) or {}
                )
                new = {attr: value for attr, value in extraction.items() if attr in missing_attributes}
                if not (entry := test_data.get(type(versioned_parser).__name__)):
                    test_data[type(versioned_parser).__name__] = new
                else:
                    entry.update(new)
                    test_data[type(versioned_parser).__name__] = dict(sorted(entry.items()))

            test_data_file.write(test_data)
            bar.update()
            subprocess.call(["git", "add", test_data_file.path], stdout=subprocess.PIPE)


if __name__ == "__main__":
    main()
