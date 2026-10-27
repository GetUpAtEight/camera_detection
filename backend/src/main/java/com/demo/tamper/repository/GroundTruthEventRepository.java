package com.demo.tamper.repository;

import com.demo.tamper.model.entity.GroundTruthEvent;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.transaction.annotation.Transactional;

public interface GroundTruthEventRepository extends JpaRepository<GroundTruthEvent, Long> {

    List<GroundTruthEvent> findByMediaIdOrderByStartMsAsc(String mediaId);

    @Transactional
    void deleteByMediaId(String mediaId);
}
