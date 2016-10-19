#!/usr/bin/env python

"""
See https://edx-wiki.atlassian.net/wiki/display/ENG/PO+File+workflow

This task extracts all English strings from all source code
and produces three human-readable files:
   conf/locale/en/LC_MESSAGES/django-partial.po
   conf/locale/en/LC_MESSAGES/djangojs-partial.po
   conf/locale/en/LC_MESSAGES/mako.po

This task will clobber any existing django.po file.
This is because django-admin.py makemessages hardcodes this filename
and it cannot be overridden.

"""

import logging
import os
import os.path
import sys
from datetime import datetime

import polib

from i18n import config, Runner
from i18n.execute import execute
from i18n.segment import segment_pofiles

EDX_MARKER = "edX translation file"
LOG = logging.getLogger(__name__)
DEVNULL = open(os.devnull, 'wb')


def base(path1, *paths):
    """Return a relative path from config.BASE_DIR to path1 / paths[0] / ... """
    return config.BASE_DIR.relpathto(path1.joinpath(*paths))  # pylint: disable=no-value-for-parameter


class Extract(Runner):
    """
    Class used to extract source files
    """

    def add_args(self):
        """
        Adds arguments
        """
        # pylint: disable=invalid-name
        self.parser.description = __doc__

    def rename_source_file(self, src, dst, locale_msg_dir):
        """
        Rename a file in the source directory.
        """
        if os.path.isfile(locale_msg_dir.joinpath(src)):
            os.rename(locale_msg_dir.joinpath(src), locale_msg_dir.joinpath(dst))
        else:
            print '{file} doesn\'t exist to rename'.format(file=src)

    def run_babel_extraction(self, outputfile_name, babel_cfg_name, babel_verbosity, stderr):
        # --keyword informs Babel that `interpolate()` is an expected
        # gettext function, which is necessary because the `tokenize` function
        # in the `markey` module marks it as such and passes it to Babel.
        # (These functions are called in the django-babel-underscore module.)
        babel_extract_template = (
            'pybabel {verbosity} extract --mapping={config} '
            '--add-comments="Translators:" --keyword="interpolate" '
            '. --output={output}'
        )
        babel_init_template = (
            'pybabel init -D {file_name} -i {input} -d {base_dir} -l {locale}'
        )
        babel_update_template = (
            'pybabel update -D {file_name} -i {input} -d {base_dir}'
        )

        babel_cfg = base(config.LOCALE_DIR, babel_cfg_name)
        outputfile_path = base(config.LOCALE_DIR, outputfile_name + '.po')
        locale_dir = base(config.LOCALE_DIR)

        if babel_cfg.exists():
            # extract strings to outputfile_name
            babel_cmd = babel_extract_template.format(
                verbosity=babel_verbosity,
                config=babel_cfg,
                output=outputfile_path
            )
            execute(babel_cmd, working_directory=config.BASE_DIR, stderr=stderr)

            for locale in config.CONFIGURATION.locales:
                locale_msg_dir = config.CONFIGURATION.get_messages_dir(locale)
                # creating translation catalog should only occur once
                if os.path.isfile(base(locale_msg_dir, outputfile_name + '.po')):
                    continue
                else:
                    babel_cmd = babel_init_template.format(
                        file_name=outputfile_name,
                        input=outputfile_path,
                        base_dir=locale_dir,
                        locale=locale
                    )
                    execute(babel_cmd, working_directory=config.BASE_DIR, stderr=stderr)

            babel_cmd = babel_update_template.format(
                file_name=outputfile_name,
                input=outputfile_path,
                base_dir=locale_dir,
            )
            execute(babel_cmd, working_directory=config.BASE_DIR, stderr=stderr)

            if os.path.isfile(outputfile_path):
                os.remove(base(outputfile_path))

    def run(self, args):
        """
        Main entry point of script
        """
        locales = config.CONFIGURATION.locales
        logging.basicConfig(stream=sys.stdout, level=logging.INFO)
        config.LOCALE_DIR.parent.makedirs_p()

        verbosity_map = {
            0: "-q",
            1: "",
            2: "-v",
        }
        babel_verbosity = verbosity_map.get(args.verbose, "")

        if args.verbose:
            stderr = None
        else:
            stderr = DEVNULL

        self.run_babel_extraction('mako', 'babel_mako.cfg', babel_verbosity, stderr)
        self.run_babel_extraction('underscore', 'babel_underscore.cfg', babel_verbosity, stderr)

        for locale in locales:
            # The extraction process clobbers django.po and djangojs.po.
            # Save them so that it won't do that.
            locale_msg_dir = config.CONFIGURATION.get_messages_dir(locale)
            self.rename_source_file('django.po', 'django-saved.po', locale_msg_dir)
            self.rename_source_file('djangojs.po', 'djangojs-saved.po', locale_msg_dir)

            makemessages = "django-admin.py makemessages -l {locale} -v{verbosity}" \
                .format(locale=locale, verbosity=args.verbose)
            ignores = " ".join('--ignore="{}/*"'.format(d) for d in config.CONFIGURATION.ignore_dirs)
            if ignores:
                makemessages += " " + ignores

            # Extract strings from django source files (*.py, *.html, *.txt).
            make_django_cmd = makemessages + ' -d django'
            execute(make_django_cmd, working_directory=config.BASE_DIR, stderr=stderr)

            # Extract strings from Javascript source files (*.js).
            make_djangojs_cmd = makemessages + ' -d djangojs'
            execute(make_djangojs_cmd, working_directory=config.BASE_DIR, stderr=stderr)

            # makemessages creates 'django.po'. This filename is hardcoded.
            # Rename it to django-partial.po to enable merging into django.po later.
            self.rename_source_file('django.po', 'django-partial.po', locale_msg_dir)
            # makemessages creates 'djangojs.po'. This filename is hardcoded.
            # Rename it to djangojs-partial.po to enable merging into djangojs.po later.
            self.rename_source_file('djangojs.po', 'djangojs-partial.po', locale_msg_dir)

            files_to_clean = set()

            # Segment the generated files.
            segmented_files = segment_pofiles(locale)
            files_to_clean.update(segmented_files)

            # Finish each file.
            for filename in files_to_clean:
                LOG.info('Cleaning %s', filename)
                pofile = polib.pofile(locale_msg_dir.joinpath(filename))
                # replace default headers with edX headers
                fix_header(pofile)
                # replace default metadata with edX metadata
                fix_metadata(pofile)
                # remove key strings which belong in messages.po
                strip_key_strings(pofile)
                pofile.save()

            # Restore the saved .po files.
            self.rename_source_file('django-saved.po', 'django.po', locale_msg_dir)
            self.rename_source_file('djangojs-saved.po', 'djangojs.po', locale_msg_dir)


def fix_header(pofile):
    """
    Replace default headers with edX headers
    """

    # By default, django-admin.py makemessages creates this header:
    #
    #   SOME DESCRIPTIVE TITLE.
    #   Copyright (C) YEAR THE PACKAGE'S COPYRIGHT HOLDER
    #   This file is distributed under the same license as the PACKAGE package.
    #   FIRST AUTHOR <EMAIL@ADDRESS>, YEAR.

    pofile.metadata_is_fuzzy = []  # remove [u'fuzzy']
    header = pofile.header
    fixes = (
        ('SOME DESCRIPTIVE TITLE', EDX_MARKER),
        ('Translations template for PROJECT.', EDX_MARKER),
        ('YEAR', str(datetime.utcnow().year)),
        ('ORGANIZATION', 'edX'),
        ("THE PACKAGE'S COPYRIGHT HOLDER", "EdX"),
        (
            'This file is distributed under the same license as the PROJECT project.',
            'This file is distributed under the GNU AFFERO GENERAL PUBLIC LICENSE.'
        ),
        (
            'This file is distributed under the same license as the PACKAGE package.',
            'This file is distributed under the GNU AFFERO GENERAL PUBLIC LICENSE.'
        ),
        ('FIRST AUTHOR <EMAIL@ADDRESS>', 'EdX Team <info@edx.org>'),
    )
    for src, dest in fixes:
        header = header.replace(src, dest)
    pofile.header = header


def fix_metadata(pofile):
    """
    Replace default metadata with edX metadata
    """

    # By default, django-admin.py makemessages creates this metadata:
    #
    #   {u'PO-Revision-Date': u'YEAR-MO-DA HO:MI+ZONE',
    #   u'Language': u'',
    #   u'Content-Transfer-Encoding': u'8bit',
    #   u'Project-Id-Version': u'PACKAGE VERSION',
    #   u'Report-Msgid-Bugs-To': u'',
    #   u'Last-Translator': u'FULL NAME <EMAIL@ADDRESS>',
    #   u'Language-Team': u'LANGUAGE <LL@li.org>',
    #   u'POT-Creation-Date': u'2013-04-25 14:14-0400',
    #   u'Content-Type': u'text/plain; charset=UTF-8',
    #   u'MIME-Version': u'1.0'}

    fixes = {
        'PO-Revision-Date': datetime.utcnow(),
        'Report-Msgid-Bugs-To': 'openedx-translation@googlegroups.com',
        'Project-Id-Version': '0.1a',
        'Language': 'en',
        'Last-Translator': '',
        'Language-Team': 'openedx-translation <openedx-translation@googlegroups.com>',
    }
    pofile.metadata.update(fixes)


def strip_key_strings(pofile):
    """
    Removes all entries in PO which are key strings.
    These entries should appear only in messages.po, not in any other po files.
    """
    newlist = [entry for entry in pofile if not is_key_string(entry.msgid)]
    del pofile[:]
    pofile += newlist


def is_key_string(string):
    """
    returns True if string is a key string.
    Key strings begin with underscore.
    """
    return len(string) > 1 and string[0] == '_'


main = Extract()

if __name__ == '__main__':
    main()
