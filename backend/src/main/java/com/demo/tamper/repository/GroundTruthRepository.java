package com.demo.tamper.repository;

import com.demo.tamper.model.entity.GroundTruthFrame;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.transaction.annotation.Transactional;

public interface GroundTruthRepository extends JpaRepository<GroundTruthFrame, Long> {

    List<GroundTruthFrame> findByTaskIdOrderByFrameIndexAsc(String taskId);

    @Transactional
    @Modifying
    void deleteByTaskId(String taskId);

    long countByTaskId(String taskId);
}
