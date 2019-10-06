.. _why-kitipy:

Why kitipy?
===========

kitipy has been built out of the desire to share more easily bits of automation
across teams and projects at `KNPLabs <https://www.knplabs.com>`_. Before
building and using it, we were overly relying on copy/paste of Makefiles and
bash scripts to share these bits. These have the downside that spreading
good/best-practices regarding automation is a real pain in the ass.

Moreover, most task runners have deficient CLI features that make them not much
more better than plain Makefiles.

So the 4 major features of kitipy are:

* Easy to compose ;
* Hence, easy to share bits of workflows or whole workflows ;
* Advanced but expressive CLI features (confirmation helpers, bash/zsh
  autocompletion, easy text styling, etc...) ;
* Support for nested commands (e.g. ``./tasks.py prod api deploy``) ;

As the last point is of particular interest for us, we decided to offload that
part to the really great Click project, maintained by Pallets team (the same
that maintain Flask and Jinja), as it aligns perfectly with our needs. Once
this library was picked, everything has be built around, so writing kitipy
tasks is as easy as writing Click commands.

If you want to know more about *Why Click?*, see
`this doc page <https://click.palletsprojects.com/en/7.x/why/>`_.


Why not fabric/invoke?
----------------------

First of all, fabric and invoke hardly tick all the boxes above. Especially,
fabric/invoke don't support nested commands, whereas this is a central feature
in kitipy.

Also, as of writing, fabric/invoke codebase is in a declining state (less than
10 commits in the first 9 months of 2019 for fabric and ~50 for invoke) and it
has no typings in its codebase, which makes the onboarding of new people more
complicated (especially people who don't have prior experience with Python).

Moreover, the difference between fabric and invoke is hard to get from the 
documentation and it uses too much magic to be easy to grasp in a couple of
minutes. As a last measure: ``concepts`` folder from invoke documentation is
+1500 lines long. Again, that looks unreasonable for anyone wanting to
introduce this tool and its features to team mates in a couple of minutes.
