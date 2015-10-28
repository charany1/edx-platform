"""
...
"""

from student.tests.factories import CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from courseware.access import has_access

from course_blocks.api import get_course_blocks


class CourseStructureTestCase(ModuleStoreTestCase):
    """
    Helper for test cases that need to build course structures.
    """
    def setUp(self):
        """
        Create users.
        """
        super(CourseStructureTestCase, self).setUp()
        # Set up users.
        self.password = 'test'
        self.user = UserFactory.create(password=self.password)
        self.staff = UserFactory.create(password=self.password, is_staff=True)

    def create_block_id(self, block_type, block_ref):
        """
        Returns the block id (display name) that is used in the test
        course structures for the given block type and block reference string.
        """
        return '{}_{}'.format(block_type, block_ref)

    def build_xblock(self, block_hierarchy, block_map, parent):
        """
        Build an XBlock, add it to block_map, and call build_xblock on the
        children defined in block_dict.

        Arguments:
            block_hierarchy (BlockStructureDict): Definition of hierarchy,
                from this block down.
            block_map (dict[str: XBlock]): Mapping from '#ref' values to their XBlocks.
            parent (XBlock): Parent block for this xBlock.
        """
        block_type = block_hierarchy['#type']
        block_ref = block_hierarchy['#ref']
        factory = (CourseFactory if block_type == 'course' else ItemFactory)
        kwargs = {key: value for key, value in block_hierarchy.iteritems() if key[0] != '#'}

        if block_type != 'course':
            kwargs['category'] = block_type
        if parent:
            kwargs['parent'] = parent

        xblock = factory.create(
            display_name=self.create_block_id(block_type, block_ref),
            publish_item=True,
            **kwargs
        )
        block_map[block_ref] = xblock

        for child_hierarchy in block_hierarchy.get('#children', []):
            self.build_xblock(child_hierarchy, block_map, xblock)

    def add_parents(self, block_hierarchy, block_map):
        """
        Recursively traverse the block_hierarchy and add additional parents.
        This method is expected to be called only after all blocks have been created.

        The additional parents are obtained from the '#parents' field
        and is expected to be a list of '#ref' values of the parents.

        Note: if a '#parents' field is found, the block is removed from
        the course block since it is expected to not belong to the root.
        If the block is meant to be a direct child of the course as well,
        the course should be explicitly listed in '#parents'.

        Arguments:
            block_hierarchy (BlockStructureDict): Definition of block hierarchy.
            block_map (dict[str: XBlock]): Mapping from '#ref' values to their XBlocks.

        """
        parents = block_hierarchy.get('#parents', [])
        if parents:
            block_key = block_map[block_hierarchy['#ref']].location

            # First remove the block from the course.
            # It would be re-added to the course if the course was
            # explicitly listed in parents.
            course = modulestore().get_item(block_map['course'].location)
            course.children.remove(block_key)
            block_map['course'] = update_block(course)

            # Add this to block to each listed parent.
            for parent_ref in parents:
                parent_block = modulestore().get_item(block_map[parent_ref].location)
                parent_block.children.append(block_key)
                block_map[parent_ref] = update_block(parent_block)

        # recursively call the children
        for child_hierarchy in block_hierarchy.get('#children', []):
            self.add_parents(child_hierarchy, block_map)

    def build_course(self, course_hierarchy):
        """
        Build a hierarchy of XBlocks.

        Arguments:
            course_hierarchy (BlockStructureDict): Definition of course hierarchy.

            where a BlockStructureDict is a list of dicts in the form {
                'key1': 'value1',
                ...
                'keyN': 'valueN',
                '#type': block_type,
                '#ref': short_string_for_referencing_block,
                '#children': list[BlockStructureDict],
                '#parents': list['#ref' values]
            }

            Special keys start with '#'; the rest just get passed as kwargs to
            Factory.create.

            Note: the caller has a choice of whether to create
            (1) a nested block structure with children blocks embedded
            within their parents, or
            (2) a flat block structure with children blocks defined
            alongside their parents and attached via the #parents field, or
            (3) a combination of both #1 and #2 used for whichever blocks.

            Note 2: When the #parents field is used in addition to the
            nested pattern for a block, it specifies additional parents
            that aren't already implied by having the block exist within
            another block's #children field.

        Returns:
            dict[str: XBlock]: Mapping from '#ref' values to their XBlocks.
        """
        block_map = {}

        # build the course tree
        for block_hierarchy in course_hierarchy:
            self.build_xblock(block_hierarchy, block_map, parent=None)

        # add additional parents if the course is a DAG or built
        # linearly (without specifying '#children' values)
        for block_hierarchy in course_hierarchy:
            self.add_parents(block_hierarchy, block_map)

        return block_map

    def get_block_key_set(self, blocks, *refs):
        """
        Gets the set of usage keys that correspond to the list of
        #ref values as defined on blocks.

        Returns: set[UsageKey]
        """
        xblocks = (blocks[ref] for ref in refs)
        return set([xblock.location for xblock in xblocks])


class BlockParentsMapTestCase(ModuleStoreTestCase):
    """
    Test helper class for creating a test course of
    a graph of vertical blocks based on a parents_map.
    """

    # Tree formed by parent_map:
    #        0
    #     /     \
    #    1       2
    #   / \     / \
    #  3   4   /   5
    #       \ /
    #        6
    # Note the parents must always have lower indices than their children.
    parents_map = [[], [0], [0], [1], [1], [2], [2, 4]]

    # TODO change this to setupClass style
    def setUp(self, **kwargs):
        super(BlockParentsMapTestCase, self).setUp()

        # create the course
        self.course = CourseFactory.create()

        # an ordered list of block locations,
        # where the index corresponds to the block's index in the parents_map.
        self.xblock_keys = [self.course.location]

        # create all other blocks in the course
        for i, parents_index in enumerate(self.parents_map):
            if i == 0:
                continue  # course already created

            # create the block as a vertical
            self.xblock_keys.append(
                ItemFactory.create(
                    parent=self.get_block(parents_index[0]),
                    category="vertical",
                ).location
            )

            # add additional parents
            if len(parents_index) > 1:
                for index in range(1, len(parents_index)):
                    parent_index = parents_index[index]
                    parent_block = self.get_block(parent_index)
                    parent_block.children.append(self.xblock_keys[i])
                    update_block(parent_block)

        self.password = 'test'
        self.student = UserFactory.create(is_staff=False, password=self.password)
        self.staff = UserFactory.create(is_staff=True, password=self.password)
        CourseEnrollmentFactory.create(is_active=True, mode='honor', user=self.student, course_id=self.course.id)

    # pylint: disable=invalid-name
    def check_transformer_results(
            self, expected_student_accessible_blocks, blocks_with_differing_student_access, transformers=None
    ):
        """
        Verifies the results of transforming the blocks in the course.

        Arguments:
            expected_student_accessible_blocks (set(int)): Set of blocks (indices) that a student
              user is expected to have access to after the transformers are executed.

            blocks_with_differing_student_access (set(int)): Set of blocks (indices) whose access
              will differ from the transformers result and the current implementation of has_access.

            transformers (BlockStructureTransformer): An optional list of transformer that are
              to be executed.  If not provided, the default value used by get_course_blocks
              is used.
        """
        def check_results(user, expected_accessible_blocks, blocks_with_differing_access):
            """
            Verifies the results of transforming the blocks in the course for the given user.
            """

            self.client.login(username=user.username, password=self.password)
            block_structure = get_course_blocks(user, self.course.location, transformers=transformers)

            # Enumerate through all the blocks that were created in the course
            for i, xblock_key in enumerate(self.xblock_keys):

                # verify existence of the block
                block_structure_result = block_structure.has_block(xblock_key)
                has_access_result = bool(has_access(user, 'load', self.get_block(i)))

                # compare with expected value
                self.assertEquals(
                    block_structure_result,
                    i in expected_accessible_blocks,
                    "block_structure return value {0} not equal to expected value for block {1}".format(
                        block_structure_result, i
                    )
                )

                # compare with has_access result
                if i in blocks_with_differing_access:
                    self.assertNotEqual(
                        block_structure_result,
                        has_access_result,
                        "block structure ({0}) and has_access ({1}) results are equal for block {2}".format(
                            block_structure_result, has_access_result, i
                        )
                    )
                else:
                    self.assertEquals(
                        block_structure_result,
                        has_access_result,
                        "block structure ({0}) and has_access ({1}) results are not equal for block {2}".format(
                            block_structure_result, has_access_result, i
                        )
                    )

            self.client.logout()

        # verify student has access to expected blocks
        check_results(self.student, expected_student_accessible_blocks, blocks_with_differing_student_access)

        # verify staff has access to all blocks
        check_results(self.staff, set(range(len(self.parents_map))), {})

    def get_block(self, block_index):
        """
        Helper method to retrieve the requested block (index) from the modulestore
        """
        return modulestore().get_item(self.xblock_keys[block_index])


def update_block(block):
    """
    Helper method to update the block in the modulestore
    """
    return modulestore().update_item(block, 'test_user')


def create_location(org, course, run, block_type, block_id):
    """
    Returns the usage key for the given key parameters using the
    default modulestore
    """
    return modulestore().make_course_key(org, course, run).make_usage_key(block_type, block_id)
